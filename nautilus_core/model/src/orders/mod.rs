// -------------------------------------------------------------------------------------------------
//  Copyright (C) 2015-2023 Nautech Systems Pty Ltd. All rights reserved.
//  https://nautechsystems.io
//
//  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
//  You may not use this file except in compliance with the License.
//  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// -------------------------------------------------------------------------------------------------

#![allow(dead_code)]

pub mod limit;

use nautilus_core::{time::UnixNanos, uuid::UUID4};
use thiserror::Error;

use crate::{
    enums::{
        ContingencyType, LiquiditySide, OrderSide, OrderStatus, OrderType, PositionSide,
        TimeInForce, TriggerType,
    },
    events::order::{
        OrderAccepted, OrderCancelRejected, OrderCanceled, OrderDenied, OrderEvent, OrderExpired,
        OrderFilled, OrderInitialized, OrderModifyRejected, OrderPendingCancel, OrderPendingUpdate,
        OrderRejected, OrderSubmitted, OrderTriggered, OrderUpdated,
    },
    identifiers::{
        account_id::AccountId, client_order_id::ClientOrderId, instrument_id::InstrumentId,
        order_list_id::OrderListId, position_id::PositionId, strategy_id::StrategyId,
        trade_id::TradeId, trader_id::TraderId, venue_order_id::VenueOrderId,
    },
    types::{fixed::fixed_i64_to_f64, price::Price, quantity::Quantity},
};

#[derive(Error, Debug)]
pub enum OrderError {
    #[error("Invalid state transition")]
    InvalidStateTransition,
    #[error("Unrecognized event")]
    UnrecognizedEvent,
}

impl OrderStatus {
    #[rustfmt::skip]
    pub fn transition(&mut self, event: &OrderEvent) -> Result<OrderStatus, OrderError> {
        let new_state = match (self, event) {
            (OrderStatus::Initialized, OrderEvent::OrderDenied(_)) => OrderStatus::Denied,
            (OrderStatus::Initialized, OrderEvent::OrderSubmitted(_)) => OrderStatus::Submitted,
            (OrderStatus::Initialized, OrderEvent::OrderRejected(_)) => OrderStatus::Rejected,  // Covers external orders
            (OrderStatus::Initialized, OrderEvent::OrderAccepted(_)) => OrderStatus::Accepted,  // Covers external orders
            (OrderStatus::Initialized, OrderEvent::OrderCanceled(_)) => OrderStatus::Canceled,  // Covers emulated and external orders
            (OrderStatus::Initialized, OrderEvent::OrderExpired(_)) => OrderStatus::Expired,  // Covers emulated and external orders
            (OrderStatus::Initialized, OrderEvent::OrderTriggered(_)) => OrderStatus::Triggered, // Covers emulated and external orders
            (OrderStatus::Submitted, OrderEvent::OrderPendingUpdate(_)) => OrderStatus::PendingUpdate,
            (OrderStatus::Submitted, OrderEvent::OrderPendingCancel(_)) => OrderStatus::PendingCancel,
            (OrderStatus::Submitted, OrderEvent::OrderRejected(_)) => OrderStatus::Rejected,
            (OrderStatus::Submitted, OrderEvent::OrderCanceled(_)) => OrderStatus::Canceled,  // Covers FOK and IOC cases
            (OrderStatus::Submitted, OrderEvent::OrderAccepted(_)) => OrderStatus::Accepted,
            (OrderStatus::Submitted, OrderEvent::OrderTriggered(_)) => OrderStatus::Triggered,  // Covers emulated StopLimit order
            (OrderStatus::Submitted, OrderEvent::OrderPartiallyFilled(_)) => OrderStatus::PartiallyFilled,
            (OrderStatus::Submitted, OrderEvent::OrderFilled(_)) => OrderStatus::Filled,
            (OrderStatus::Accepted, OrderEvent::OrderRejected(_)) => OrderStatus::Rejected,  // Covers StopLimit order
            (OrderStatus::Accepted, OrderEvent::OrderPendingUpdate(_)) => OrderStatus::PendingUpdate,
            (OrderStatus::Accepted, OrderEvent::OrderPendingCancel(_)) => OrderStatus::PendingCancel,
            (OrderStatus::Accepted, OrderEvent::OrderCanceled(_)) => OrderStatus::Canceled,
            (OrderStatus::Accepted, OrderEvent::OrderTriggered(_)) => OrderStatus::Triggered,
            (OrderStatus::Accepted, OrderEvent::OrderExpired(_)) => OrderStatus::Expired,
            (OrderStatus::Accepted, OrderEvent::OrderPartiallyFilled(_)) => OrderStatus::PartiallyFilled,
            (OrderStatus::Accepted, OrderEvent::OrderFilled(_)) => OrderStatus::Filled,
            (OrderStatus::Canceled, OrderEvent::OrderPartiallyFilled(_)) => OrderStatus::PartiallyFilled,  // Real world possibility
            (OrderStatus::Canceled, OrderEvent::OrderFilled(_)) => OrderStatus::Filled,  // Real world possibility
            (OrderStatus::PendingUpdate, OrderEvent::OrderRejected(_)) => OrderStatus::Rejected,
            (OrderStatus::PendingUpdate, OrderEvent::OrderAccepted(_)) => OrderStatus::Accepted,
            (OrderStatus::PendingUpdate, OrderEvent::OrderCanceled(_)) => OrderStatus::Canceled,
            (OrderStatus::PendingUpdate, OrderEvent::OrderExpired(_)) => OrderStatus::Expired,
            (OrderStatus::PendingUpdate, OrderEvent::OrderTriggered(_)) => OrderStatus::Triggered,
            (OrderStatus::PendingUpdate, OrderEvent::OrderPendingUpdate(_)) => OrderStatus::PendingUpdate,  // Allow multiple requests
            (OrderStatus::PendingUpdate, OrderEvent::OrderPendingCancel(_)) => OrderStatus::PendingCancel,
            (OrderStatus::PendingUpdate, OrderEvent::OrderPartiallyFilled(_)) => OrderStatus::PartiallyFilled,
            (OrderStatus::PendingUpdate, OrderEvent::OrderFilled(_)) => OrderStatus::Filled,
            (OrderStatus::PendingCancel, OrderEvent::OrderRejected(_)) => OrderStatus::Rejected,
            (OrderStatus::PendingCancel, OrderEvent::OrderPendingCancel(_)) => OrderStatus::PendingCancel,  // Allow multiple requests
            (OrderStatus::PendingCancel, OrderEvent::OrderCanceled(_)) => OrderStatus::Canceled,
            (OrderStatus::PendingCancel, OrderEvent::OrderAccepted(_)) => OrderStatus::Accepted,  // Allow failed cancel requests
            (OrderStatus::PendingCancel, OrderEvent::OrderPartiallyFilled(_)) => OrderStatus::PartiallyFilled,
            (OrderStatus::PendingCancel, OrderEvent::OrderFilled(_)) => OrderStatus::Filled,
            (OrderStatus::Triggered, OrderEvent::OrderRejected(_)) => OrderStatus::Rejected,
            (OrderStatus::Triggered, OrderEvent::OrderPendingUpdate(_)) => OrderStatus::PendingUpdate,
            (OrderStatus::Triggered, OrderEvent::OrderPendingCancel(_)) => OrderStatus::PendingCancel,
            (OrderStatus::Triggered, OrderEvent::OrderCanceled(_)) => OrderStatus::Canceled,
            (OrderStatus::Triggered, OrderEvent::OrderExpired(_)) => OrderStatus::Expired,
            (OrderStatus::Triggered, OrderEvent::OrderPartiallyFilled(_)) => OrderStatus::PartiallyFilled,
            (OrderStatus::Triggered, OrderEvent::OrderFilled(_)) => OrderStatus::Filled,
            (OrderStatus::PartiallyFilled, OrderEvent::OrderPendingUpdate(_)) => OrderStatus::PendingUpdate,
            (OrderStatus::PartiallyFilled, OrderEvent::OrderPendingCancel(_)) => OrderStatus::PendingCancel,
            (OrderStatus::PartiallyFilled, OrderEvent::OrderCanceled(_)) => OrderStatus::Canceled,
            (OrderStatus::PartiallyFilled, OrderEvent::OrderExpired(_)) => OrderStatus::Expired,
            (OrderStatus::PartiallyFilled, OrderEvent::OrderPartiallyFilled(_)) => OrderStatus::PartiallyFilled,
            (OrderStatus::PartiallyFilled, OrderEvent::OrderFilled(_)) => OrderStatus::Filled,
            _ => return Err(OrderError::InvalidStateTransition),
        };
        Ok(new_state)
    }
}

struct Order {
    events: Vec<OrderEvent>,
    venue_order_ids: Vec<VenueOrderId>, // TODO(cs): Should be `Vec<&VenueOrderId>` or similar
    trade_ids: Vec<TradeId>,            // TODO(cs): Should be `Vec<&TradeId>` or similar
    previous_status: Option<OrderStatus>,
    triggered_price: Option<Price>,
    pub status: OrderStatus,
    pub trader_id: TraderId,
    pub strategy_id: StrategyId,
    pub instrument_id: InstrumentId,
    pub client_order_id: ClientOrderId,
    pub venue_order_id: Option<VenueOrderId>,
    pub position_id: Option<PositionId>,
    pub account_id: Option<AccountId>,
    pub last_trade_id: Option<TradeId>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    pub price: Option<Price>,
    pub trigger_price: Option<Price>,
    pub trigger_type: Option<TriggerType>,
    pub time_in_force: TimeInForce,
    pub expire_time: Option<UnixNanos>,
    pub liquidity_side: Option<LiquiditySide>,
    pub is_post_only: bool,
    pub is_reduce_only: bool,
    pub is_quote_quantity: bool,
    pub display_qty: Option<Quantity>,
    pub limit_offset: Option<Price>,
    pub trailing_offset: Option<Price>,
    pub trailing_offset_type: Option<TriggerType>,
    pub emulation_trigger: Option<TriggerType>,
    pub contingency_type: Option<ContingencyType>,
    pub order_list_id: Option<OrderListId>,
    pub linked_order_ids: Option<Vec<ClientOrderId>>,
    pub parent_order_id: Option<ClientOrderId>,
    pub tags: Option<String>,
    pub filled_qty: Quantity,
    pub leaves_qty: Quantity,
    pub avg_px: Option<f64>,
    pub slippage: Option<f64>,
    pub init_id: UUID4,
    pub ts_triggered: Option<UnixNanos>,
    pub ts_init: UnixNanos,
    pub ts_last: UnixNanos,
}

impl PartialEq<Self> for Order {
    fn eq(&self, other: &Self) -> bool {
        self.client_order_id == other.client_order_id
    }
}

impl Eq for Order {}

impl From<OrderInitialized> for Order {
    fn from(value: OrderInitialized) -> Self {
        Self {
            events: Vec::new(),
            venue_order_ids: Vec::new(),
            trade_ids: Vec::new(),
            previous_status: None,
            triggered_price: None,
            status: OrderStatus::Initialized,
            trader_id: value.trader_id,
            strategy_id: value.strategy_id,
            instrument_id: value.instrument_id,
            client_order_id: value.client_order_id,
            venue_order_id: None,
            position_id: None,
            account_id: None,
            last_trade_id: None,
            side: value.order_side,
            order_type: value.order_type,
            quantity: value.quantity,
            price: value.price,
            trigger_price: value.trigger_price,
            trigger_type: value.trigger_type,
            time_in_force: value.time_in_force,
            expire_time: None,
            liquidity_side: None,
            is_post_only: value.post_only,
            is_reduce_only: value.reduce_only,
            is_quote_quantity: value.quote_quantity,
            display_qty: None,
            limit_offset: None,
            trailing_offset: None,
            trailing_offset_type: None,
            emulation_trigger: value.emulation_trigger,
            contingency_type: value.contingency_type,
            order_list_id: value.order_list_id,
            linked_order_ids: value.linked_order_ids,
            parent_order_id: value.parent_order_id,
            tags: value.tags,
            filled_qty: Quantity::new(0.0, 0),
            leaves_qty: value.quantity,
            avg_px: None,
            slippage: None,
            init_id: value.event_id,
            ts_triggered: None,
            ts_init: value.ts_event,
            ts_last: value.ts_event,
        }
    }
}

impl From<&Order> for OrderInitialized {
    fn from(value: &Order) -> Self {
        Self {
            trader_id: value.trader_id.clone(),
            strategy_id: value.strategy_id.clone(),
            instrument_id: value.instrument_id.clone(),
            client_order_id: value.client_order_id.clone(),
            order_side: value.side,
            order_type: value.order_type,
            quantity: value.quantity,
            price: value.price,
            trigger_price: value.triggered_price,
            trigger_type: value.trigger_type,
            time_in_force: value.time_in_force,
            expire_time: value.expire_time,
            post_only: value.is_post_only,
            reduce_only: value.is_reduce_only,
            quote_quantity: value.is_quote_quantity,
            display_qty: value.display_qty,
            limit_offset: value.limit_offset,
            trailing_offset: value.trailing_offset,
            trailing_offset_type: value.trailing_offset_type,
            emulation_trigger: value.emulation_trigger,
            contingency_type: value.contingency_type,
            order_list_id: value.order_list_id.clone(),
            linked_order_ids: value.linked_order_ids.clone(),
            parent_order_id: value.parent_order_id.clone(),
            tags: value.tags.clone(),
            event_id: value.init_id.clone(),
            ts_event: value.ts_init,
            ts_init: value.ts_init,
            reconciliation: false,
        }
    }
}

impl Order {
    pub fn last_event(&self) -> Option<&OrderEvent> {
        self.events.last()
    }

    pub fn events(&self) -> Vec<OrderEvent> {
        self.events.clone()
    }

    pub fn event_count(&self) -> usize {
        self.events.len()
    }

    pub fn venue_order_ids(&self) -> Vec<VenueOrderId> {
        self.venue_order_ids.clone()
    }

    pub fn trade_ids(&self) -> Vec<TradeId> {
        self.trade_ids.clone()
    }

    pub fn is_buy(&self) -> bool {
        self.side == OrderSide::Buy
    }

    pub fn is_sell(&self) -> bool {
        self.side == OrderSide::Sell
    }

    pub fn is_passive(&self) -> bool {
        self.order_type != OrderType::Market
    }

    pub fn is_aggressive(&self) -> bool {
        self.order_type == OrderType::Market
    }

    pub fn is_emulated(&self) -> bool {
        self.emulation_trigger.is_some()
    }

    pub fn is_contingency(&self) -> bool {
        self.contingency_type.is_some()
    }

    pub fn is_parent_order(&self) -> bool {
        match self.contingency_type {
            Some(c) => c == ContingencyType::Oto,
            None => false,
        }
    }

    pub fn is_child_order(&self) -> bool {
        self.parent_order_id.is_some()
    }

    pub fn is_open(&self) -> bool {
        if self.emulation_trigger.is_some() {
            return false;
        }
        self.status == OrderStatus::Accepted
            || self.status == OrderStatus::Triggered
            || self.status == OrderStatus::PendingCancel
            || self.status == OrderStatus::PendingUpdate
            || self.status == OrderStatus::PartiallyFilled
    }

    pub fn is_closed(&self) -> bool {
        self.status == OrderStatus::Denied
            || self.status == OrderStatus::Rejected
            || self.status == OrderStatus::Canceled
            || self.status == OrderStatus::Expired
            || self.status == OrderStatus::Filled
    }

    pub fn is_inflight(&self) -> bool {
        if self.emulation_trigger.is_some() {
            return false;
        }
        self.status == OrderStatus::Submitted
            || self.status == OrderStatus::PendingCancel
            || self.status == OrderStatus::PendingUpdate
    }

    pub fn is_pending_update(&self) -> bool {
        self.status == OrderStatus::PendingUpdate
    }

    pub fn is_pending_cancel(&self) -> bool {
        self.status == OrderStatus::PendingCancel
    }

    pub fn opposite_side(side: OrderSide) -> OrderSide {
        match side {
            OrderSide::Buy => OrderSide::Sell,
            OrderSide::Sell => OrderSide::Buy,
            OrderSide::NoOrderSide => OrderSide::NoOrderSide,
        }
    }

    pub fn closing_side(side: PositionSide) -> OrderSide {
        match side {
            PositionSide::Long => OrderSide::Sell,
            PositionSide::Short => OrderSide::Buy,
            PositionSide::Flat => OrderSide::NoOrderSide,
            PositionSide::NoPositionSide => OrderSide::NoOrderSide,
        }
    }

    pub fn would_reduce_only(&self, side: PositionSide, position_qty: Quantity) -> bool {
        if side == PositionSide::Flat {
            return false;
        }

        match (self.side, side) {
            (OrderSide::Buy, PositionSide::Long) => false,
            (OrderSide::Buy, PositionSide::Short) => self.leaves_qty <= position_qty,
            (OrderSide::Sell, PositionSide::Short) => false,
            (OrderSide::Sell, PositionSide::Long) => self.leaves_qty <= position_qty,
            _ => true,
        }
    }

    pub fn apply(&mut self, event: OrderEvent) -> Result<(), OrderError> {
        let new_status = self.status.transition(&event)?;
        self.previous_status = Some(self.status);
        self.status = new_status;

        match &event {
            OrderEvent::OrderDenied(event) => self.denied(event),
            OrderEvent::OrderSubmitted(event) => self.submitted(event),
            OrderEvent::OrderRejected(event) => self.rejected(event),
            OrderEvent::OrderAccepted(event) => self.accepted(event),
            OrderEvent::OrderPendingUpdate(event) => self.pending_update(event),
            OrderEvent::OrderPendingCancel(event) => self.pending_cancel(event),
            OrderEvent::OrderModifyRejected(event) => self.modify_rejected(event),
            OrderEvent::OrderCancelRejected(event) => self.cancel_rejected(event),
            OrderEvent::OrderUpdated(event) => self.updated(event),
            OrderEvent::OrderTriggered(event) => self.triggered(event),
            OrderEvent::OrderCanceled(event) => self.canceled(event),
            OrderEvent::OrderExpired(event) => self.expired(event),
            _ => return Err(OrderError::UnrecognizedEvent),
        }

        self.events.push(event);
        Ok(())
    }

    fn denied(&self, _event: &OrderDenied) {
        // Do nothing else
    }

    fn submitted(&mut self, event: &OrderSubmitted) {
        self.account_id = Some(event.account_id.clone())
    }

    fn accepted(&mut self, event: &OrderAccepted) {
        self.venue_order_id = Some(event.venue_order_id.clone());
    }

    fn rejected(&self, _event: &OrderRejected) {
        // Do nothing else
    }

    fn pending_update(&self, _event: &OrderPendingUpdate) {
        // Do nothing else
    }

    fn pending_cancel(&self, _event: &OrderPendingCancel) {
        // Do nothing else
    }

    fn modify_rejected(&mut self, _event: &OrderModifyRejected) {
        self.status = self.previous_status.unwrap();
    }

    fn cancel_rejected(&mut self, _event: &OrderCancelRejected) {
        self.status = self.previous_status.unwrap();
    }

    fn triggered(&mut self, _event: &OrderTriggered) {}

    fn canceled(&mut self, _event: &OrderCanceled) {}

    fn expired(&mut self, _event: &OrderExpired) {}

    fn updated(&mut self, event: &OrderUpdated) {
        match &event.venue_order_id {
            Some(venue_order_id) => {
                if self.venue_order_id.is_some()
                    && venue_order_id != self.venue_order_id.as_ref().unwrap()
                {
                    self.venue_order_id = Some(venue_order_id.clone());
                    self.venue_order_ids.push(venue_order_id.clone()); // TODO(cs): Temporary clone
                }
            }
            None => {}
        }
        if let Some(price) = &event.price {
            if self.price.is_some() {
                self.price.replace(*price);
            } else {
                panic!("invalid update of `price` when None")
            }
        }

        if let Some(trigger_price) = &event.trigger_price {
            if self.trigger_price.is_some() {
                self.trigger_price.replace(*trigger_price);
            } else {
                panic!("invalid update of `trigger_price` when None")
            }
        }

        self.quantity.raw = event.quantity.raw;
        self.leaves_qty = Quantity::from_raw(
            self.quantity.raw - self.filled_qty.raw,
            self.quantity.precision,
        );
    }

    fn filled(&mut self, event: &OrderFilled) {
        self.venue_order_id = Some(event.venue_order_id.clone());
        self.position_id = event.position_id.clone();
        self.trade_ids.push(event.trade_id.clone());
        self.last_trade_id = Some(event.trade_id.clone());
        self.liquidity_side = Some(event.liquidity_side);
        self.filled_qty += &event.last_qty;
        self.leaves_qty -= &event.last_qty;
        self.ts_last = event.ts_event;
        self.set_avg_px(&event.last_qty, &event.last_px);
        self.set_slippage();
    }

    fn set_avg_px(&mut self, last_qty: &Quantity, last_px: &Price) {
        if self.avg_px.is_none() {
            self.avg_px = Some(last_px.as_f64());
        }

        let filled_qty = self.filled_qty.as_f64();
        let total_qty = filled_qty + last_qty.as_f64();

        let avg_px = self
            .avg_px
            .unwrap()
            .mul_add(filled_qty, last_px.as_f64() * last_qty.as_f64())
            / total_qty;
        self.avg_px = Some(avg_px);
    }

    fn set_slippage(&mut self) {
        self.slippage = self.avg_px.and_then(|avg_px| {
            self.price
                .as_ref()
                .map(|price| fixed_i64_to_f64(price.raw))
                .and_then(|price| match self.side {
                    OrderSide::Buy if avg_px > price => Some(avg_px - price),
                    OrderSide::Sell if avg_px < price => Some(price - avg_px),
                    _ => None,
                })
        })
    }
}

////////////////////////////////////////////////////////////////////////////////
// Tests
////////////////////////////////////////////////////////////////////////////////
#[cfg(test)]
mod tests {
    use rstest::rstest;

    use super::*;
    use crate::{
        enums::{OrderSide, OrderStatus, PositionSide},
        events::order::{
            OrderAcceptedBuilder, OrderDeniedBuilder, OrderEvent, OrderInitializedBuilder,
            OrderSubmittedBuilder,
        },
    };

    #[test]
    fn test_order_initialized() {
        let order: Order = OrderInitializedBuilder::default().build().unwrap().into();

        assert_eq!(order.status, OrderStatus::Initialized);
        assert_eq!(order.last_event(), None);
        assert_eq!(order.event_count(), 0);
        assert!(order.venue_order_ids.is_empty());
        assert!(order.trade_ids.is_empty());
        assert!(order.is_buy());
        assert!(!order.is_sell());
        assert!(!order.is_passive());
        assert!(order.is_aggressive());
        assert!(!order.is_emulated());
        assert!(!order.is_contingency());
        assert!(!order.is_parent_order());
        assert!(!order.is_child_order());
        assert!(!order.is_open());
        assert!(!order.is_closed());
        assert!(!order.is_inflight());
        assert!(!order.is_pending_update());
        assert!(!order.is_pending_cancel());
    }

    #[rstest(
        order_side,
        expected_side,
        case(OrderSide::Buy, OrderSide::Sell),
        case(OrderSide::Sell, OrderSide::Buy),
        case(OrderSide::NoOrderSide, OrderSide::NoOrderSide)
    )]
    fn test_order_opposite_side(order_side: OrderSide, expected_side: OrderSide) {
        let result = Order::opposite_side(order_side);
        assert_eq!(result, expected_side)
    }

    #[rstest(
        position_side,
        expected_side,
        case(PositionSide::Long, OrderSide::Sell),
        case(PositionSide::Short, OrderSide::Buy),
        case(PositionSide::NoPositionSide, OrderSide::NoOrderSide)
    )]
    fn test_closing_side(position_side: PositionSide, expected_side: OrderSide) {
        let result = Order::closing_side(position_side);
        assert_eq!(result, expected_side)
    }

    #[rustfmt::skip]
    #[rstest(
        order_side, order_qty, position_side, position_qty, expected,
        case(OrderSide::Buy, Quantity::from(100), PositionSide::Long, Quantity::from(50), false),
        case(OrderSide::Buy, Quantity::from(50), PositionSide::Short, Quantity::from(50), true),
        case(OrderSide::Buy, Quantity::from(50), PositionSide::Short, Quantity::from(100), true),
        case(OrderSide::Buy, Quantity::from(50), PositionSide::Flat, Quantity::from(0), false),
        case(OrderSide::Sell, Quantity::from(50), PositionSide::Flat, Quantity::from(0), false),
        case(OrderSide::Sell, Quantity::from(50), PositionSide::Long, Quantity::from(50), true),
        case(OrderSide::Sell, Quantity::from(50), PositionSide::Long, Quantity::from(100), true),
        case(OrderSide::Sell, Quantity::from(100), PositionSide::Short, Quantity::from(50), false),
    )]
    fn test_would_reduce_only(
        order_side: OrderSide,
        order_qty: Quantity,
        position_side: PositionSide,
        position_qty: Quantity,
        expected: bool,
    ) {
        let order: Order = OrderInitializedBuilder::default()
            .order_side(order_side)
            .quantity(order_qty)
            .build()
            .unwrap()
            .into();

        assert_eq!(
            order.would_reduce_only(position_side, position_qty),
            expected
        );
    }

    #[test]
    fn test_order_state_transition_denied() {
        let init = OrderInitializedBuilder::default().build().unwrap();
        let denied = OrderDeniedBuilder::default().build().unwrap();
        let mut order: Order = init.into();
        let event = OrderEvent::OrderDenied(denied);

        let _ = order.apply(event.clone());

        assert_eq!(order.status, OrderStatus::Denied);
        assert!(order.is_closed());
        assert!(!order.is_open());
        assert_eq!(order.event_count(), 1);
        assert_eq!(order.last_event(), Some(&event));
    }

    #[test]
    fn test_buy_order_life_cyle_to_filled() {
        // TODO: We should be able to derive defaults for the below?
        let init = OrderInitializedBuilder::default().build().unwrap();
        let submitted = OrderSubmittedBuilder::default().build().unwrap();
        let accepted = OrderAcceptedBuilder::default().build().unwrap();
        // let filled = OrderFilledBuilder::default()
        //     .ids(init.ids.clone())
        //     .account_id(AccountId::default())
        //     .venue_order_id(VenueOrderId::default())
        //     .position_id(None)
        //     .trade_id(TradeId::new("001"))
        //     .event_id(UUID4::default())
        //     .ts_event(UnixNanos::default())
        //     .ts_init(UnixNanos::default())
        //     .reconciliation(false)
        //     .build()
        //     .unwrap();

        let client_order_id = init.client_order_id.clone();
        let mut order: Order = init.into();
        let _ = order.apply(OrderEvent::OrderSubmitted(submitted));
        let _ = order.apply(OrderEvent::OrderAccepted(accepted));
        // let _ = order.apply(OrderEvent::OrderFilled(filled));

        assert_eq!(order.client_order_id, client_order_id);
    }
}
