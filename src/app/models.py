from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Any, Generic, TypeVar, Union
from enum import Enum
from datetime import datetime, timezone
import uuid

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"

class TimeInForce(str, Enum):
    GTC = "GTC"  # Good 'til Canceled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill

class OrderStatus(str, Enum):
    PENDING = "pending"
    PLACED = "placed"
    PARTIALLY_FILLED = "partially_filled"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"

# --- API Models ---

class CreateOrderRequest(BaseModel):
    trade_id: Union[int, str] = Field(..., description="Globally unique trade ID")
    account_id: Union[int, str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[float] = None
    quantity: int
    time_in_force: TimeInForce = TimeInForce.GTC

class TradeOrder(BaseModel):
    trade_id: Union[int, str]
    symbol: str
    quantity: int
    side: OrderSide
    order_type: OrderType = OrderType.MARKET

class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: int
    trade_id: Union[int, str]
    account_id: Union[int, str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[float] = None
    quantity: int
    time_in_force: TimeInForce
    status: OrderStatus
    broker_order_id: Optional[str] = None
    reason: Optional[str] = None
    executed_quantity: int = 0
    avg_execution_price: Optional[float] = None
    executed_at: Optional[datetime] = None

class CreateOrderResponse(OrderResponse):
    """Alias for OrderResponse to match central contract naming."""
    pass

class ExecutionResult(BaseModel):
    status: OrderStatus
    broker_order_id: Optional[str] = None
    symbol: str
    side: OrderSide
    quantity: int
    avg_execution_price: Optional[float] = None
    executed_at: Optional[datetime] = None
    reason: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    broker_connected: bool
    mode: str

# --- Standard Response Models ---

class ErrorDetail(BaseModel):
    code: str
    message: str

T = TypeVar("T")

class StandardAgentResponse(BaseModel, Generic[T]):
    status: str  # "success" or "error"
    agent_type: str = "execution"
    data: Optional[T] = None
    error: Optional[dict] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence_score: Optional[float] = None

# --- Internal Models ---

class Order(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: int
    trade_id: Union[int, str]
    account_id: Union[int, str]
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[float] = None
    quantity: int
    time_in_force: TimeInForce

    # --- State Fields ---
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: Optional[str] = None
    reason: Optional[str] = None
    executed_quantity: int = 0
    avg_execution_price: Optional[float] = None
    executed_at: Optional[datetime] = None
