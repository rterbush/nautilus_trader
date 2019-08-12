# -------------------------------------------------------------------------------------------------
# <copyright file="trader.pxd" company="Nautech Systems Pty Ltd">
#  Copyright (C) 2015-2019 Nautech Systems Pty Ltd. All rights reserved.
#  The use of this source code is governed by the license as found in the LICENSE.md file.
#  https://nautechsystems.io
# </copyright>
# -------------------------------------------------------------------------------------------------

from nautilus_trader.core.typed_collections cimport TypedList
from nautilus_trader.common.account cimport Account
from nautilus_trader.common.clock cimport Clock
from nautilus_trader.common.logger cimport LoggerAdapter
from nautilus_trader.common.data cimport DataClient
from nautilus_trader.common.execution cimport ExecutionClient
from nautilus_trader.model.identifiers cimport IdTag, TraderId
from nautilus_trader.trade.portfolio cimport Portfolio
from nautilus_trader.trade.reports cimport ReportProvider


cdef class Trader:
    """
    Provides a trader for managing a portfolio of trading strategies.
    """
    cdef Clock _clock
    cdef LoggerAdapter _log
    cdef DataClient _data_client
    cdef ExecutionClient _exec_client
    cdef ReportProvider _report_provider

    cdef readonly TraderId id
    cdef readonly IdTag id_tag_trader
    cdef readonly Account account
    cdef readonly Portfolio portfolio
    cdef readonly bint is_running
    cdef readonly TypedList started_datetimes
    cdef readonly TypedList stopped_datetimes
    cdef readonly TypedList strategies

    cpdef load_strategies(self, list strategies)
    cpdef start(self)
    cpdef stop(self)
    cpdef reset(self)
    cpdef dispose(self)

    cpdef dict strategy_status(self)
    cpdef void create_returns_tear_sheet(self)
    cpdef void create_full_tear_sheet(self)
    cpdef object get_orders_report(self)
    cpdef object get_order_fills_report(self)
    cpdef object get_positions_report(self)
