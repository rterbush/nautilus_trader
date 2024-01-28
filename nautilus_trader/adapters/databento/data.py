# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2024 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

import asyncio
from collections import defaultdict
from collections.abc import Coroutine
from typing import Any

import pandas as pd
import pytz

from nautilus_trader.adapters.databento.common import databento_schema_from_nautilus_bar_type
from nautilus_trader.adapters.databento.config import DatabentoDataClientConfig
from nautilus_trader.adapters.databento.constants import ALL_SYMBOLS
from nautilus_trader.adapters.databento.constants import DATABENTO_CLIENT_ID
from nautilus_trader.adapters.databento.constants import PUBLISHERS_PATH
from nautilus_trader.adapters.databento.enums import DatabentoRecordFlags
from nautilus_trader.adapters.databento.loaders import DatabentoDataLoader
from nautilus_trader.adapters.databento.providers import DatabentoInstrumentProvider
from nautilus_trader.adapters.databento.types import Dataset
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import OrderBookDepth10
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import bar_aggregation_to_str
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import instruments_from_pyo3


class DatabentoDataClient(LiveMarketDataClient):
    """
    Provides a data client for the `Databento` API.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    http_client : nautilus_pyo3.DatabentoHistoricalClient
        The Databento historical HTTP client.
    msgbus : MessageBus
        The message bus for the client.
    cache : Cache
        The cache for the client.
    clock : LiveClock
        The clock for the client.
    loader : DatabentoDataLoader, optional
        The loader for the client.
    config : DatabentoDataClientConfig, optional
        The configuration for the client.

    Warnings
    --------
    This class should not be used directly, but through a concrete subclass.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        http_client: nautilus_pyo3.DatabentoHistoricalClient,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: DatabentoInstrumentProvider,
        loader: DatabentoDataLoader | None = None,
        config: DatabentoDataClientConfig | None = None,
    ) -> None:
        if config is None:
            config = DatabentoDataClientConfig()
        PyCondition.type(config, DatabentoDataClientConfig, "config")

        super().__init__(
            loop=loop,
            client_id=DATABENTO_CLIENT_ID,
            venue=None,  # Not applicable
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
        )

        # Configuration
        self._live_api_key: str = config.api_key or http_client.key
        self._live_gateway: str | None = config.live_gateway
        self._parent_symbols: dict[Dataset, set[str]] = defaultdict(set)
        self._instrument_ids: dict[Dataset, set[InstrumentId]] = defaultdict(set)
        self._timeout_initial_load: float | None = config.timeout_initial_load
        self._mbo_subscriptions_delay: float | None = config.mbo_subscriptions_delay

        # Clients
        self._http_client = http_client
        self._live_clients: dict[Dataset, nautilus_pyo3.DatabentoLiveClient] = {}
        self._live_clients_mbo: dict[Dataset, nautilus_pyo3.DatabentoLiveClient] = {}
        self._has_subscribed: dict[Dataset, bool] = {}
        self._loader = loader or DatabentoDataLoader()
        self._dataset_ranges: dict[Dataset, tuple[pd.Timestamp, pd.Timestamp]] = {}
        self._dataset_ranges_requested: set[Dataset] = set()

        # Cache parent symbol index
        for dataset, parent_symbols in (config.parent_symbols or {}).items():
            self._parent_symbols[dataset].update(set(parent_symbols))

        # Cache instrument index
        for instrument_id in config.instrument_ids or []:
            dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
            self._instrument_ids[dataset].add(instrument_id)

        # MBO/L3 subscription buffering
        self._buffer_mbo_subscriptions_task: asyncio.Task | None = None
        self._is_buffering_mbo_subscriptions: bool = bool(config.mbo_subscriptions_delay)
        self._buffered_mbo_subscriptions: dict[Dataset, list[InstrumentId]] = defaultdict(list)
        self._buffered_deltas: dict[InstrumentId, list[OrderBookDelta]] = defaultdict(list)

        # Tasks
        self._live_client_futures: set[asyncio.Future] = set()
        self._update_dataset_ranges_interval_seconds: int = 60 * 60  # Once per hour (hard coded)
        self._update_dataset_ranges_task: asyncio.Task | None = None

    async def _connect(self) -> None:
        if not self._instrument_ids:
            return  # Nothing else to do yet

        if self._is_buffering_mbo_subscriptions:
            self._buffer_mbo_subscriptions_task = self.create_task(self._buffer_mbo_subscriptions())

        self._log.info("Initializing instruments...")

        coros: list[Coroutine] = []
        for dataset, instrument_ids in self._instrument_ids.items():
            loading_ids: list[InstrumentId] = sorted(instrument_ids)
            coros.append(self._instrument_provider.load_ids_async(instrument_ids=loading_ids))
            await self._subscribe_instrument_ids(dataset, instrument_ids=loading_ids)

        try:
            if self._timeout_initial_load:
                await asyncio.wait_for(asyncio.gather(*coros), timeout=self._timeout_initial_load)
            else:
                await asyncio.gather(*coros)
        except asyncio.TimeoutError:
            self._log.warning("Timeout waiting for instruments...")

        self._send_all_instruments_to_data_engine()
        self._update_dataset_ranges_task = self.create_task(self._update_dataset_ranges())

    async def _disconnect(self) -> None:
        if self._buffer_mbo_subscriptions_task:
            self._log.debug("Canceling `buffer_mbo_subscriptions` task...")
            self._buffer_mbo_subscriptions_task.cancel()
            self._buffer_mbo_subscriptions_task = None

        # Cancel update dataset ranges task
        if self._update_dataset_ranges_task:
            self._log.debug("Canceling `update_dataset_ranges` task...")
            self._update_dataset_ranges_task.cancel()
            self._update_dataset_ranges_task = None

        # Close all live clients
        futures: list[asyncio.Future] = []
        for dataset, live_client in self._live_clients.items():
            self._log.info(f"Stopping {dataset} live feed...", LogColor.BLUE)
            future = asyncio.ensure_future(live_client.close())
            futures.append(future)

        for dataset, live_client in self._live_clients_mbo.items():
            self._log.info(f"Stopping {dataset} MBO/L3 live feed...", LogColor.BLUE)
            future = asyncio.ensure_future(live_client.close())
            futures.append(future)

        await asyncio.gather(*futures)

    async def _update_dataset_ranges(self) -> None:
        while True:
            try:
                self._log.debug(
                    f"Scheduled `update_instruments` to run in "
                    f"{self._update_dataset_ranges_interval_seconds}s.",
                )

                await asyncio.sleep(self._update_dataset_ranges_interval_seconds)

                tasks = []
                for dataset in self._dataset_ranges:
                    tasks.append(self._get_dataset_range(dataset))

                await asyncio.gather(*tasks)
            except Exception as e:  # Create specific exception type
                self._log.error(f"Error updating dataset range: {e}")
            except asyncio.CancelledError:
                self._log.debug("Canceled `update_dataset_ranges` task.")
                break

    async def _buffer_mbo_subscriptions(self) -> None:
        try:
            await asyncio.sleep(self._mbo_subscriptions_delay or 0.0)
            self._is_buffering_mbo_subscriptions = False

            coros: list[Coroutine] = []
            for dataset, instrument_ids in self._buffered_mbo_subscriptions.items():
                self._log.info(f"Starting {dataset} MBO/L3 live feeds...")
                coro = self._subscribe_order_book_deltas_batch(instrument_ids)
                coros.append(coro)

            await asyncio.gather(*coros)
        except asyncio.CancelledError:
            self._log.debug("Canceled `buffer_mbo_subscriptions` task.")

    def _get_live_client(self, dataset: Dataset) -> nautilus_pyo3.DatabentoLiveClient:
        # Retrieve or initialize the 'general' live client for the specified dataset
        live_client = self._live_clients.get(dataset)

        if live_client is None:
            live_client = nautilus_pyo3.DatabentoLiveClient(
                key=self._live_api_key,
                dataset=dataset,
                publishers_path=str(PUBLISHERS_PATH),
            )
            self._live_clients[dataset] = live_client

        return live_client

    def _get_live_client_mbo(self, dataset: Dataset) -> nautilus_pyo3.DatabentoLiveClient:
        # Retrieve or initialize the 'MBO/L3' live client for the specified dataset
        live_client = self._live_clients_mbo.get(dataset)

        if live_client is None:
            live_client = nautilus_pyo3.DatabentoLiveClient(
                key=self._live_api_key,
                dataset=dataset,
                publishers_path=str(PUBLISHERS_PATH),
            )
            self._live_clients_mbo[dataset] = live_client

        return live_client

    def _check_live_client_started(
        self,
        dataset: Dataset,
        live_client: nautilus_pyo3.DatabentoLiveClient,
    ) -> None:
        if not self._has_subscribed.get(dataset):
            self._log.debug(f"Starting {dataset} live client...", LogColor.MAGENTA)
            future = asyncio.ensure_future(live_client.start(callback=self._handle_record))
            self._live_client_futures.add(future)
            self._has_subscribed[dataset] = True
            self._log.info(f"Started {dataset} live feed.", LogColor.BLUE)

    def _send_all_instruments_to_data_engine(self) -> None:
        for instrument in self._instrument_provider.get_all().values():
            self._handle_data(instrument)

    async def _ensure_subscribed_for_instrument(self, instrument_id: InstrumentId) -> None:
        try:
            dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
            subscribed_instruments = self._instrument_ids[dataset]

            if instrument_id in subscribed_instruments:
                return

            self._instrument_ids[dataset].add(instrument_id)
            await self._subscribe_instrument(instrument_id)
        except asyncio.CancelledError:
            self._log.warning(
                "`_ensure_subscribed_for_instrument` was canceled while still pending.",
            )

    async def _get_dataset_range(
        self,
        dataset: Dataset,
    ) -> tuple[pd.Timestamp | None, pd.Timestamp]:
        # Check and cache dataset available range
        while dataset in self._dataset_ranges_requested:
            await asyncio.sleep(0.1)

        available_range = self._dataset_ranges.get(dataset)
        if available_range:
            return available_range

        self._dataset_ranges_requested.add(dataset)

        try:
            self._log.info(f"Requesting dataset range for {dataset}...", LogColor.BLUE)
            response = await self._http_client.get_dataset_range(dataset)

            available_start = pd.Timestamp(response["start_date"], tz=pytz.utc)
            available_end = pd.Timestamp(response["end_date"], tz=pytz.utc)

            self._dataset_ranges[dataset] = (available_start, available_end)

            self._log.info(
                f"Dataset {dataset} available end {available_end.date()}.",
                LogColor.BLUE,
            )

            return available_start, available_end
        except asyncio.CancelledError:
            self._log.warning("`_get_dataset_range` was canceled while still pending.")
            return (None, pd.Timestamp.utcnow())
        except Exception as e:  # More specific exception
            self._log.error(f"Error requesting dataset range: {e}")
            return (None, pd.Timestamp.utcnow())
        finally:
            self._dataset_ranges_requested.discard(dataset)

    # -- OVERRIDES ----------------------------------------------------------------------------

    def subscribe_order_book_deltas(
        self,
        instrument_id: InstrumentId,
        book_type: BookType,
        depth: int | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        if book_type != BookType.L3_MBO:
            raise NotImplementedError

        self.create_task(
            self._subscribe_order_book_deltas(
                instrument_id=instrument_id,
                book_type=book_type,
                depth=depth,
                kwargs=kwargs,
            ),
            log_msg=f"subscribe: order_book_deltas {instrument_id}",
            actions=lambda: self._add_subscription_order_book_deltas(instrument_id),
        )

    # -- SUBSCRIPTIONS ----------------------------------------------------------------------------

    async def _subscribe(self, data_type: DataType) -> None:
        # Replace method in child class, for exchange specific data types.
        raise NotImplementedError(f"Cannot subscribe to {data_type.type} (not implemented).")

    async def _subscribe_instruments(self) -> None:
        # Replace method in child class, for exchange specific data types.
        raise NotImplementedError("Cannot subscribe to all instruments (not currently supported).")

    async def _subscribe_instrument(self, instrument_id: InstrumentId) -> None:
        try:
            dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
            live_client = self._get_live_client(dataset)
            await live_client.subscribe(
                schema="definition",
                symbols=instrument_id.symbol.value,
            )
            self._check_live_client_started(dataset, live_client)
        except asyncio.CancelledError:
            self._log.warning("`_subscribe_instrument` was canceled while still pending.")

    async def _subscribe_parent_symbols(
        self,
        dataset: Dataset,
        parent_symbols: set[str],
    ) -> None:
        try:
            live_client = self._get_live_client(dataset)
            await live_client.subscribe(
                schema="definition",
                symbols=",".join(sorted(parent_symbols)),
                stype_in="parent",
            )
            self._check_live_client_started(dataset, live_client)
        except asyncio.CancelledError:
            self._log.warning("`_subscribe_parent_symbols` was canceled while still pending.")

    async def _subscribe_instrument_ids(
        self,
        dataset: Dataset,
        instrument_ids: list[InstrumentId],
    ) -> None:
        try:
            live_client = self._get_live_client(dataset)
            await live_client.subscribe(
                schema="definition",
                symbols=",".join(sorted([i.symbol.value for i in instrument_ids])),
            )
            self._check_live_client_started(dataset, live_client)
        except asyncio.CancelledError:
            self._log.warning("`_subscribe_instrument_ids` was canceled while still pending.")

    async def _subscribe_order_book_deltas(
        self,
        instrument_id: InstrumentId,
        book_type: BookType,
        depth: int | None = None,
        kwargs: dict | None = None,
    ) -> None:
        try:
            if book_type != BookType.L3_MBO:
                raise NotImplementedError

            if depth:  # Can be None or 0 (full depth)
                self._log.error(
                    f"Cannot subscribe to order book deltas with specific depth of {depth} "
                    "(do not specify depth when subscribing, must be full depth).",
                )
                return

            dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)

            if self._is_buffering_mbo_subscriptions:
                self._log.debug(
                    f"Buffering MBO/L3 subscription for {instrument_id}.",
                    LogColor.MAGENTA,
                )
                self._buffered_mbo_subscriptions[dataset].append(instrument_id)
                return

            if self._get_live_client_mbo(dataset) is not None:
                self._log.error(
                    f"Cannot subscribe to order book deltas for {instrument_id}, "
                    "MBO/L3 feed already started.",
                )
                return

            await self._subscribe_order_book_deltas_batch([instrument_id])
        except asyncio.CancelledError:
            self._log.warning("`_subscribe_order_book_deltas` was canceled while still pending.")

    async def _subscribe_order_book_deltas_batch(
        self,
        instrument_ids: list[InstrumentId],
    ) -> None:
        try:
            if not instrument_ids:
                self._log.warning(
                    "No subscriptions for order book deltas (`instrument_ids` was empty).",
                )
                return

            for instrument_id in instrument_ids:
                if not self._cache.instrument(instrument_id):
                    self._log.error(
                        f"Cannot subscribe to order book deltas for {instrument_id}, "
                        "instrument must be pre-loaded via the `DatabentoDataClientConfig` "
                        "or a specific subscription on start.",
                    )
                    instrument_ids.remove(instrument_id)
                    continue

            if not instrument_ids:
                return  # No subscribing instrument IDs were loaded in the cache

            dataset: Dataset = self._loader.get_dataset_for_venue(instrument_ids[0].venue)
            live_client = self._get_live_client_mbo(dataset)
            await live_client.subscribe(
                schema="mbo",
                symbols=",".join(sorted([i.symbol.value for i in instrument_ids])),
                start=0,  # Must subscribe from start of week to get 'Sunday snapshot' for now
            )
            future = asyncio.ensure_future(live_client.start(self._handle_record))
            self._live_client_futures.add(future)
        except asyncio.CancelledError:
            self._log.warning(
                "`_subscribe_order_book_deltas_batch` was canceled while still pending.",
            )

    async def _subscribe_order_book_snapshots(
        self,
        instrument_id: InstrumentId,
        book_type: BookType,
        depth: int | None = None,
        kwargs: dict | None = None,
    ) -> None:
        try:
            await self._ensure_subscribed_for_instrument(instrument_id)

            match depth:
                case 1:
                    schema = "mbp-1"
                case 10:
                    schema = "mbp-10"
                case _:
                    self._log.error(
                        f"Cannot subscribe for order book snapshots of depth {depth}, use either 1 or 10.",
                    )
                    return

            dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
            live_client = self._get_live_client(dataset)
            await live_client.subscribe(
                schema=schema,
                symbols=",".join(sorted([instrument_id.symbol.value])),
            )
            self._check_live_client_started(dataset, live_client)
        except asyncio.CancelledError:
            self._log.warning("`_subscribe_order_book_snapshots` was canceled while still pending.")

    async def _subscribe_quote_ticks(self, instrument_id: InstrumentId) -> None:
        try:
            await self._ensure_subscribed_for_instrument(instrument_id)

            dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
            live_client = self._get_live_client(dataset)
            await live_client.subscribe(
                schema="mbp-1",
                symbols=",".join(sorted([instrument_id.symbol.value])),
            )
            self._check_live_client_started(dataset, live_client)
        except asyncio.CancelledError:
            self._log.warning("`_subscribe_quote_ticks` was canceled while still pending.")

    async def _subscribe_trade_ticks(self, instrument_id: InstrumentId) -> None:
        try:
            if instrument_id in self.subscribed_quote_ticks():
                return  # Already subscribed for trades

            await self._ensure_subscribed_for_instrument(instrument_id)

            dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
            live_client = self._get_live_client(dataset)
            await live_client.subscribe(
                schema="trades",
                symbols=instrument_id.symbol.value,
            )
            self._check_live_client_started(dataset, live_client)
        except asyncio.CancelledError:
            self._log.warning("`_subscribe_trade_ticks` was canceled while still pending.")

    async def _subscribe_bars(self, bar_type: BarType) -> None:
        try:
            dataset: Dataset = self._loader.get_dataset_for_venue(bar_type.instrument_id.venue)

            try:
                schema = databento_schema_from_nautilus_bar_type(bar_type)
            except ValueError as e:
                self._log.error(f"Cannot subscribe: {e}")
                return

            live_client = self._get_live_client(dataset)
            await live_client.subscribe(
                schema=schema,
                symbols=bar_type.instrument_id.symbol.value,
            )
            self._check_live_client_started(dataset, live_client)
        except asyncio.CancelledError:
            self._log.warning("`_subscribe_bars` was canceled while still pending.")

    async def _unsubscribe(self, data_type: DataType) -> None:
        raise NotImplementedError(
            f"Cannot unsubscribe from {data_type} (not implemented).",
        )

    async def _unsubscribe_instruments(self) -> None:
        raise NotImplementedError(
            "Cannot unsubscribe from all instruments, unsubscribing not supported by Databento.",
        )

    async def _unsubscribe_instrument(self, instrument_id: InstrumentId) -> None:
        raise NotImplementedError(
            f"Cannot unsubscribe from {instrument_id} instrument, "
            "unsubscribing not supported by Databento.",
        )

    async def _unsubscribe_order_book_deltas(self, instrument_id: InstrumentId) -> None:
        raise NotImplementedError(
            f"Cannot unsubscribe from {instrument_id} order book deltas, "
            "unsubscribing not supported by Databento.",
        )

    async def _unsubscribe_order_book_snapshots(self, instrument_id: InstrumentId) -> None:
        raise NotImplementedError(
            f"Cannot unsubscribe from {instrument_id} order book snapshots, "
            "unsubscribing not supported by Databento.",
        )

    async def _unsubscribe_quote_ticks(self, instrument_id: InstrumentId) -> None:
        raise NotImplementedError(
            f"Cannot unsubscribe from {instrument_id} quote ticks, "
            "unsubscribing not supported by Databento.",
        )

    async def _unsubscribe_trade_ticks(self, instrument_id: InstrumentId) -> None:
        raise NotImplementedError(
            f"Cannot unsubscribe from {instrument_id} trade ticks, "
            "unsubscribing not supported by Databento.",
        )

    async def _unsubscribe_bars(self, bar_type: BarType) -> None:
        raise NotImplementedError(
            f"Cannot unsubscribe from {bar_type} bars, "
            "unsubscribing not supported by Databento.",
        )

    async def _request(self, data_type: DataType, correlation_id: UUID4) -> None:
        raise NotImplementedError(
            f"Cannot request {data_type} (not implemented).",
        )

    async def _request_instrument(
        self,
        instrument_id: InstrumentId,
        correlation_id: UUID4,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> None:
        dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
        _, available_end = await self._get_dataset_range(dataset)

        start = start or available_end - pd.Timedelta(days=2)
        end = end or available_end

        self._log.info(
            f"Requesting {instrument_id} instrument definitions: "
            f"dataset={dataset}, start={start}, end={end}",
            LogColor.BLUE,
        )

        pyo3_instruments = await self._http_client.get_range_instruments(
            dataset=dataset,
            symbols=ALL_SYMBOLS,
            start=start.value,
            end=end.value,
        )

        instruments = instruments_from_pyo3(pyo3_instruments)

        self._handle_instruments(
            instruments=instruments,
            correlation_id=correlation_id,
        )

    async def _request_instruments(
        self,
        venue: Venue,
        correlation_id: UUID4,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> None:
        dataset: Dataset = self._loader.get_dataset_for_venue(venue)
        _, available_end = await self._get_dataset_range(dataset)

        start = start or available_end - pd.Timedelta(days=2)
        end = end or available_end

        self._log.info(
            f"Requesting {venue} instrument definitions: "
            f"dataset={dataset}, start={start}, end={end}",
            LogColor.BLUE,
        )

        pyo3_instruments = await self._http_client.get_range_instruments(
            dataset=dataset,
            symbols=ALL_SYMBOLS,
            start=start.value,
            end=end.value,
        )

        instruments = instruments_from_pyo3(pyo3_instruments)

        self._handle_instruments(
            instruments=instruments,
            venue=venue,
            correlation_id=correlation_id,
        )

    async def _request_quote_ticks(
        self,
        instrument_id: InstrumentId,
        limit: int,
        correlation_id: UUID4,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> None:
        dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
        _, available_end = await self._get_dataset_range(dataset)

        start = start or available_end - pd.Timedelta(days=1)
        end = end or available_end

        if limit > 0:
            self._log.warning(
                f"Ignoring limit {limit} because its applied from the start (instead of the end).",
            )

        self._log.info(
            f"Requesting {instrument_id} quote ticks: "
            f"dataset={dataset}, start={start}, end={end}",
            LogColor.BLUE,
        )

        pyo3_quotes = await self._http_client.get_range_quotes(
            dataset=dataset,
            symbols=instrument_id.symbol.value,
            start=start.value,
            end=end.value,
        )

        quotes = QuoteTick.from_pyo3_list(pyo3_quotes)

        self._handle_quote_ticks(
            instrument_id=instrument_id,
            ticks=quotes,
            correlation_id=correlation_id,
        )

    async def _request_trade_ticks(
        self,
        instrument_id: InstrumentId,
        limit: int,
        correlation_id: UUID4,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> None:
        dataset: Dataset = self._loader.get_dataset_for_venue(instrument_id.venue)
        _, available_end = await self._get_dataset_range(dataset)

        start = start or available_end - pd.Timedelta(days=1)
        end = end or available_end

        if limit > 0:
            self._log.warning(
                f"Ignoring limit {limit} because its applied from the start (instead of the end).",
            )

        self._log.info(
            f"Requesting {instrument_id} trade ticks: "
            f"dataset={dataset}, start={start}, end={end}",
            LogColor.BLUE,
        )

        pyo3_trades = await self._http_client.get_range_trades(
            dataset=dataset,
            symbols=instrument_id.symbol.value,
            start=(start or available_end - pd.Timedelta(days=1)).value,
            end=(end or available_end).value,
        )

        trades = TradeTick.from_pyo3_list(pyo3_trades)

        self._handle_trade_ticks(
            instrument_id=instrument_id,
            ticks=trades,
            correlation_id=correlation_id,
        )

    async def _request_bars(
        self,
        bar_type: BarType,
        limit: int,
        correlation_id: UUID4,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> None:
        dataset: Dataset = self._loader.get_dataset_for_venue(bar_type.instrument_id.venue)
        _, available_end = await self._get_dataset_range(dataset)

        start = start or available_end - pd.Timedelta(days=1)
        end = end or available_end

        if limit > 0:
            self._log.warning(
                f"Ignoring limit {limit} because its applied from the start (instead of the end).",
            )

        self._log.info(
            f"Requesting {bar_type.instrument_id} 1 {bar_aggregation_to_str(bar_type.spec.aggregation)} bars: "
            f"dataset={dataset}, start={start}, end={end}",
            LogColor.BLUE,
        )

        pyo3_bars = await self._http_client.get_range_bars(
            dataset=dataset,
            symbols=bar_type.instrument_id.symbol.value,
            aggregation=nautilus_pyo3.BarAggregation(
                bar_aggregation_to_str(bar_type.spec.aggregation),
            ),
            start=(start or available_end - pd.Timedelta(days=1)).value,
            end=(end or available_end).value,
        )

        bars = Bar.from_pyo3_list(pyo3_bars)

        self._handle_bars(
            bar_type=bar_type,
            bars=bars,
            partial=None,  # No partials
            correlation_id=correlation_id,
        )

    def _handle_record(
        self,
        pyo3_data: Any,  # TODO: Define `Data` base class
    ) -> None:
        # self._log.debug(f"Received {record}", LogColor.MAGENTA)

        if isinstance(pyo3_data, nautilus_pyo3.OrderBookDelta):
            instrument_id = InstrumentId.from_str(pyo3_data.instrument_id.value)

            data = OrderBookDelta.from_pyo3_list([pyo3_data])  # TODO: Implement single function
            if DatabentoRecordFlags.F_LAST not in DatabentoRecordFlags(data.flags):
                buffer = self._buffered_deltas[instrument_id]
                buffer.append(data)
                return  # We can rely on the F_LAST flag for an MBO feed
            else:
                buffer = self._buffered_deltas[instrument_id]
                buffer.append(data)
                data = OrderBookDeltas(instrument_id, deltas=buffer.copy())
                buffer.clear()
        elif isinstance(pyo3_data, nautilus_pyo3.OrderBookDepth10):
            data = OrderBookDepth10.from_pyo3_list([pyo3_data])  # TODO: Implement single function
        elif isinstance(pyo3_data, nautilus_pyo3.QuoteTick):
            data = QuoteTick.from_pyo3_list([pyo3_data])  # TODO: Implement single function
        elif isinstance(pyo3_data, nautilus_pyo3.TradeTick):
            data = TradeTick.from_pyo3_list([pyo3_data])  # TODO: Implement single function
        elif isinstance(pyo3_data, nautilus_pyo3.Bar):
            data = Bar.from_pyo3_list([pyo3_data])  # TODO: Implement single function
        else:
            self._log.error(f"Unimplemented data type in handler: {pyo3_data}.")
            return

        self._handle_data(data)
