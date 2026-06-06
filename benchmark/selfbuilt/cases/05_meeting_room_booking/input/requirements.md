# 会议室预约系统需求说明

## 1. 项目背景

开发一个小型会议室预约 API，供团队内部管理会议室资源。用户可以查询会议室、创建预约、按日期查看预约、取消预约。系统不需要前端页面，不需要登录鉴权，重点是 API 行为、时间冲突检测和 SQLite 持久化。

Agent 必须从空 `workspace/` 开始创建完整 Python 项目。

## 2. 技术约束

- 项目语言：Python 3.11+。
- 项目形态：Flask API。
- 包名：`meeting_room_booking`。
- 必须暴露工厂函数：`create_app(db_path=None)`。
- 持久化方式：SQLite。
- 每次数据库操作结束后必须关闭 SQLite 连接；仅使用 `with sqlite3.connect(...) as conn` 不会关闭 `sqlite3.Connection`，应使用显式 `close()`、`try/finally` 或 `contextlib.closing`，确保 Windows 下临时数据库文件不会被连接占用。
- Agent 需要自行创建 `workspace/requirements.txt`，包含 Flask 依赖，例如 `Flask>=3.0,<4.0`。
- `db_path=None` 时，默认使用当前工作目录下的 `meeting_rooms.db`。

## 3. 时间格式

所有预约时间使用本地时间字符串：

```text
YYYY-MM-DD HH:MM
```

示例：

```text
2026-06-10 09:00
```

规则：

- 开始时间必须早于结束时间。
- 同一会议室中，时间段只要有重叠就不能预约。
- 边界相接不算冲突，例如 `09:00-10:00` 和 `10:00-11:00` 可以同时存在。

重叠判断：

```text
new_start < existing_end and new_end > existing_start
```

只与状态为 `active` 的预约检测冲突，已取消预约不参与冲突检测。

## 4. API 接口

### 4.1 健康检查

```http
GET /health
```

响应：

```json
{"status": "ok"}
```

### 4.2 创建会议室

```http
POST /rooms
Content-Type: application/json
```

请求：

```json
{
  "name": "Room A",
  "capacity": 8,
  "location": "2F"
}
```

规则：

- `name` 必填，非空。
- `capacity` 必填，正整数。
- `location` 可选，默认空字符串。
- 会议室名称唯一。

成功：

- 状态码：201。
- 响应为会议室对象，包含 `id`、`name`、`capacity`、`location`。

失败：

- 参数错误：400，`{"error": "<message>"}`。
- 名称重复：409，`{"error": "room already exists"}`。

### 4.3 查看会议室

```http
GET /rooms
```

成功返回会议室数组，按 `id` 升序。

### 4.4 创建预约

```http
POST /bookings
Content-Type: application/json
```

请求：

```json
{
  "room_id": 1,
  "user": "Ada",
  "title": "Weekly Sync",
  "start": "2026-06-10 09:00",
  "end": "2026-06-10 10:00"
}
```

规则：

- `room_id` 必填，必须存在。
- `user` 必填，非空。
- `title` 必填，非空。
- `start` 和 `end` 必填，格式正确且 start < end。
- 同一会议室 active 预约不能重叠。

成功：

- 状态码：201。
- 响应为预约对象，包含 `id`、`room_id`、`user`、`title`、`start`、`end`、`status`。
- 新预约 `status` 为 `active`。

失败：

- 参数错误：400。
- 会议室不存在：404，`{"error": "room not found"}`。
- 时间冲突：409，`{"error": "booking conflict"}`。

### 4.5 查询预约

```http
GET /bookings?date=2026-06-10
GET /bookings?room_id=1
GET /bookings?date=2026-06-10&room_id=1
```

规则：

- `date` 可选，格式 `YYYY-MM-DD`。
- `room_id` 可选。
- 返回 active 预约。
- 按 start 升序，再按 id 升序。

### 4.6 取消预约

```http
DELETE /bookings/<id>
```

规则：

- 预约存在且 active 时，状态改为 `cancelled`。
- 取消成功返回 204，无响应体。
- 预约不存在返回 404。
- 已取消预约再次取消返回 404。

## 5. 错误响应

所有错误响应均为 JSON：

```json
{"error": "message"}
```

不要向客户端泄漏 Python traceback。
