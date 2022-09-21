# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2022 Nautech Systems Pty Ltd. All rights reserved.
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


from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data.tick import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


# *** THIS IS A TEST STRATEGY ***


class SignalStrategyConfig(StrategyConfig):
    """
    Configuration for ``SignalStrategy`` instances.
    """

    instrument_id: str


class SignalStrategy(Strategy):
    """
    A strategy that simply emits a signal counter (FOR TESTING PURPOSES ONLY)

    Parameters
    ----------
    config : OrderbookImbalanceConfig
        The configuration for the instance.
    """

    def __init__(self, config: SignalStrategyConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(self.config.instrument_id)
        self.counter = 0

    def on_start(self):
        """Actions to be performed on strategy start."""
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_trade_ticks(instrument_id=self.instrument_id)

    def on_trade_tick(self, tick: TradeTick):
        self.counter += 1
        self.publish_signal(name="counter", value=self.counter, ts_event=tick.ts_event)
