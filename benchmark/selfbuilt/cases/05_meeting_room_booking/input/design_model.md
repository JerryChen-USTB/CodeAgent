# 会议室预约系统设计模型

## 1. 类图

```mermaid
classDiagram
    class Room {
      +int id
      +str name
      +int capacity
      +str location
    }

    class Booking {
      +int id
      +int room_id
      +str user
      +str title
      +datetime start
      +datetime end
      +str status
    }

    class BookingRepository {
      +init_db() None
      +create_room(data) Room
      +list_rooms() list~Room~
      +create_booking(data) Booking
      +list_bookings(date, room_id) list~Booking~
      +cancel_booking(id) bool
      +find_conflict(room_id, start, end) Booking?
    }

    class BookingService {
      +validate_room_payload(data)
      +validate_booking_payload(data)
      +create_room(data) Room
      +create_booking(data) Booking
      +cancel_booking(id) bool
    }

    class FlaskApp {
      +create_app(db_path) Flask
    }

    Room "1" --> "*" Booking
    BookingService --> BookingRepository
    FlaskApp --> BookingService
```

## 2. 创建预约流程

```mermaid
flowchart TD
    A["POST /bookings"] --> B["解析 JSON"]
    B --> C{"字段是否合法?"}
    C -- "否" --> D["400 error"]
    C -- "是" --> E["查询 room_id"]
    E --> F{"会议室存在?"}
    F -- "否" --> G["404 room not found"]
    F -- "是" --> H["查询 active 冲突预约"]
    H --> I{"是否冲突?"}
    I -- "是" --> J["409 booking conflict"]
    I -- "否" --> K["写入 SQLite"]
    K --> L["201 返回预约 JSON"]
```

## 3. 预约状态机

```mermaid
stateDiagram-v2
    [*] --> active: create booking
    active --> cancelled: DELETE /bookings/id
    cancelled --> [*]
```

## 4. API 时序图

```mermaid
sequenceDiagram
    participant Client
    participant Flask
    participant Service
    participant Repository
    participant SQLite

    Client->>Flask: POST /bookings
    Flask->>Service: validate and create booking
    Service->>Repository: find_conflict(room_id,start,end)
    Repository->>SQLite: SELECT active bookings
    SQLite-->>Repository: conflict or none
    Repository-->>Service: result
    Service->>Repository: create_booking
    Repository->>SQLite: INSERT booking
    Repository-->>Service: booking
    Service-->>Flask: booking
    Flask-->>Client: 201 JSON
```

## 5. 模块建议

- `meeting_room_booking/__init__.py`：导出 `create_app`。
- `meeting_room_booking/app.py`：Flask 路由。
- `meeting_room_booking/service.py`：校验和业务规则。
- `meeting_room_booking/repository.py`：SQLite 访问。
