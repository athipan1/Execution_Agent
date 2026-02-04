# Execution Agent

โปรเจกต์นี้เป็นไมโครเซอร์วิสที่พัฒนาด้วย FastAPI ทำหน้าที่เป็น **Execution Agent** ในระบบเทรดอัตโนมัติ โดยมีหน้าที่รับคำสั่งซื้อขาย (Trade Requests) ส่งต่อคำสั่งไปยังโบรกเกอร์ผ่านระบบ Adapter ที่ยืดหยุ่น และบันทึกสถานะการทำงานทั้งหมดผ่าน Database Agent ระบบนี้ถูกออกแบบมาให้มีความปลอดภัย รองรับ Idempotency มีความสามารถในการตรวจสอบย้อนกลับ (Observability) และทดสอบได้ง่าย

## คุณสมบัติหลัก

- **FastAPI Application**: พัฒนาด้วย FastAPI ที่รองรับการทำงานแบบ Asynchronous ประสิทธิภาพสูง
- **Idempotent Order Creation**: ป้องกันการส่งคำสั่งซื้อขายซ้ำซ้อนด้วย Idempotency Key
- **Pluggable Broker Adapters**: รองรับการเชื่อมต่อกับโบรกเกอร์ที่หลากหลายผ่าน Interface ที่กำหนดไว้ (เช่น Alpaca และ Simulator สำหรับทดสอบ)
- **Asynchronous Execution**: ประมวลผลการส่งคำสั่งในพื้นหลัง (Background Task) เพื่อให้ API ตอบสนองได้อย่างรวดเร็ว
- **Database Agent Integration**: เก็บข้อมูลสถานะคำสั่งซื้อขายผ่าน Database Agent ภายนอกซึ่งเป็น Single Source of Truth
- **Structured Logging**: บันทึก Log ในรูปแบบ JSON เพื่อความสะดวกในการวิเคราะห์ข้อมูล
- **API Key Security**: ป้องกันการเข้าถึง API ด้วย Middleware ตรวจสอบ API Key

---

## การทำงานของระบบ (System Workflow)

1. **รับคำสั่ง**: เมื่อได้รับคำสั่งซื้อขายผ่าน Endpoint `/execute` ระบบจะตรวจสอบ `Idempotency-Key` (หรือ `trade_id`) เพื่อป้องกันคำสั่งซ้ำ
2. **บันทึกสถานะเริ่มต้น**: ระบบจะบันทึกคำสั่งซื้อขายที่มีสถานะเป็น `pending` ลงในฐานข้อมูลผ่าน Database Agent
3. **ส่งคำสั่งไปยังโบรกเกอร์**: ระบบจะเริ่มกระบวนการส่งคำสั่งในพื้นหลัง (Background Task) เพื่อไม่ให้ผู้ใช้งานต้องรอนาน
4. **อัปเดตสถานะ**: เมื่อโบรกเกอร์ตอบรับหรือปฏิเสธคำสั่ง `ExecutionService` จะทำการอัปเดตสถานะล่าสุด (เช่น `placed`, `failed`) กลับไปยังฐานข้อมูล

---

## การเชื่อมต่อกับโบรกเกอร์ (Broker Connection)

โปรเจกต์นี้รองรับการเชื่อมต่อกับโบรกเกอร์ผ่าน **Broker Adapters** โดยปัจจุบันรองรับ:

### 1. Alpaca (AlpacaAdapter)
- **การยืนยันตัวตน**: ใช้ `APCA-API-KEY-ID` และ `APCA-API-SECRET-KEY` ส่งผ่าน HTTP Headers
- **Endpoint**: เชื่อมต่อไปยัง Alpaca Broker API (เช่น `v2/orders` สำหรับส่งคำสั่ง และ `v2/account` สำหรับตรวจสอบการเชื่อมต่อ)
- **การทำงาน**: ระบบจะส่งคำสั่งแบบ Market Order ไปยัง Alpaca และรอรับ Broker Order ID เพื่อนำมาอัปเดตในระบบ

### 2. Simulator (SimulatorAdapter)
- ใช้สำหรับการทดสอบโดยไม่ต้องเชื่อมต่อกับโบรกเกอร์จริง
- มีพฤติกรรมที่กำหนดไว้ล่วงหน้า (Deterministic) เช่น หากส่งสัญลักษณ์ `FAIL.BK` คำสั่งจะล้มเหลวเสมอ เพื่อใช้ในการทดสอบเคสต่างๆ

---

## รายละเอียด API Endpoints

### 1. สร้างคำสั่งซื้อขาย
- **Method**: `POST`
- **Path**: `/execute`
- **รายละเอียด**: รับคำสั่งซื้อขายและเริ่มกระบวนการส่งคำสั่ง
- **Header พิเศษ**: `Idempotency-Key` (Optional) - ใช้เพื่อป้องกันการส่งคำสั่งซ้ำ

### 2. ตรวจสอบสถานะคำสั่งซื้อขาย
- **Method**: `GET`
- **Path**: `/execute/{order_id}`
- **รายละเอียด**: ดึงข้อมูลและสถานะล่าสุดของคำสั่งซื้อขายจากฐานข้อมูลตาม `order_id`

### 3. ยกเลิกคำสั่งซื้อขาย
- **Method**: `POST`
- **Path**: `/execute/{order_id}/cancel`
- **รายละเอียด**: ขอยกเลิกคำสั่งซื้อขายที่ยังทำงานไม่สำเร็จไปยังโบรกเกอร์

### 4. ตรวจสอบความพร้อมของระบบ
- **Method**: `GET`
- **Path**: `/health`
- **รายละเอียด**: ตรวจสอบว่าบริการ Execution Agent ยังทำงานอยู่ปกติหรือไม่

### 5. ตรวจสอบการเชื่อมต่อกับ Alpaca
- **Method**: `GET`
- **Path**: `/health/alpaca`
- **รายละเอียด**: ตรวจสอบว่าระบบสามารถเชื่อมต่อกับ Alpaca API ได้สำเร็จหรือไม่

---

## รายละเอียด Data Schema

### 1. CreateOrderRequest (คำขอสร้างคำสั่งซื้อขาย)
| ฟิลด์ | ประเภท | คำอธิบาย |
| :--- | :--- | :--- |
| `trade_id` | `Union[int, str]` | รหัสคำสั่งซื้อขาย (ต้องไม่ซ้ำกัน) |
| `account_id` | `Union[int, str]` | รหัสบัญชีผู้ใช้งาน |
| `symbol` | `str` | สัญลักษณ์หลักทรัพย์ (เช่น AAPL, BTC/USD) |
| `side` | `OrderSide` | ด้านที่ต้องการซื้อขาย (`buy` หรือ `sell`) |
| `order_type` | `OrderType` | ประเภทคำสั่ง (`market` หรือ `limit`) |
| `price` | `float` (Optional) | ราคาที่ต้องการ (สำหรับคำสั่งแบบ `limit`) |
| `quantity` | `int` | จำนวนที่ต้องการซื้อขาย |
| `time_in_force` | `TimeInForce` | ระยะเวลาที่คำสั่งมีผล (เช่น `GTC`, `IOC`, `FOK`) |

### 2. OrderResponse (การตอบกลับหลังจากรับคำสั่ง)
| ฟิลด์ | ประเภท | คำอธิบาย |
| :--- | :--- | :--- |
| `order_id` | `int` | รหัสคำสั่งซื้อขายในระบบ (Database ID) |
| `trade_id` | `Union[int, str]` | รหัสคำสั่งซื้อขาย |
| `status` | `OrderStatus` | สถานะปัจจุบันของคำสั่ง |
| `broker_order_id` | `str` (Optional) | รหัสคำสั่งซื้อขายจากฝั่งโบรกเกอร์ |
| `reason` | `str` (Optional) | เหตุผลเพิ่มเติม (กรณีคำสั่งล้มเหลว) |

### 3. Order (ข้อมูลคำสั่งซื้อขายฉบับเต็ม)
| ฟิลด์ | ประเภท | คำอธิบาย |
| :--- | :--- | :--- |
| `order_id` | `int` | รหัสคำสั่งซื้อขายในระบบ |
| `trade_id` | `Union[int, str]` | รหัสคำสั่งซื้อขาย |
| `account_id` | `Union[int, str]` | รหัสบัญชีผู้ใช้งาน |
| `symbol` | `str` | สัญลักษณ์หลักทรัพย์ |
| `side` | `OrderSide` | ด้านที่ต้องการซื้อขาย |
| `order_type` | `OrderType` | ประเภทคำสั่ง |
| `price` | `float` | ราคาที่ระบุ (ถ้ามี) |
| `quantity` | `int` | จำนวนที่ระบุ |
| `time_in_force` | `TimeInForce` | ระยะเวลาที่คำสั่งมีผล |
| `status` | `OrderStatus` | สถานะของคำสั่ง (`pending`, `placed`, `executed`, ฯลฯ) |
| `broker_order_id` | `str` | รหัสคำสั่งจากโบรกเกอร์ |
| `reason` | `str` | เหตุผลในกรณีที่เกิดข้อผิดพลาด |
| `executed_quantity` | `int` | จำนวนที่จับคู่ได้แล้วจริง |
| `avg_execution_price` | `float` | ราคาเฉลี่ยที่จับคู่ได้ |

---

## การตั้งค่าและการติดตั้ง

### 1. ติดตั้ง Dependencies
แนะนำให้ใช้งานผ่าน virtual environment:
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. ตั้งค่า Environment Variables
สร้างไฟล์ `.env` ที่ root ของโปรเจกต์:
```ini
API_KEY="your-secret-api-key"
BROKER_MODE="SIMULATOR" # หรือ "ALPACA"
ALPACA_API_KEY_ID="your-alpaca-key"
ALPACA_SECRET_KEY="your-alpaca-secret"
ALPACA_API_URL="https://paper-api.alpaca.markets"
```

## การรันโปรเจกต์
ใช้ `uvicorn` เพื่อรันเซิร์ฟเวอร์:
```bash
PYTHONPATH=src uvicorn app.main:app --reload
```

## การรัน Test
ใช้ `pytest` เพื่อตรวจสอบความถูกต้องของระบบ:
```bash
PYTHONPATH=src python -m pytest
```
