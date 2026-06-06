# 会议室预约系统验收标准

## AC-01 Flask 工厂函数和依赖

给定 Agent 已在空 `workspace/` 中实现 `meeting_room_booking` 包：

- `from meeting_room_booking import create_app` 可以导入。
- `create_app(db_path=...)` 返回 Flask app。
- `workspace/requirements.txt` 包含 Flask 3 兼容依赖，例如 `Flask>=3.0,<4.0`。
- 数据库路径可注入，便于测试隔离。

## AC-02 Web UI 首页

`GET /` 返回 HTML：

- 状态码 200。
- 页面包含 `会议室预约系统`。
- 页面包含会议室管理和预约管理入口。
- 页面可以继续进入创建会议室、创建预约、查询预约和取消预约流程。

## AC-03 Web UI 创建会议室

当通过 `POST /ui/rooms` 表单提交：

| 字段 | 值 |
| --- | --- |
| `name` | `Room A` |
| `capacity` | `8` |
| `location` | `2F` |

则：

- 响应状态为成功或重定向后的成功页。
- HTML 正文包含 `Room A`。
- `GET /ui/rooms` 能看到 `Room A`、`8`、`2F`。

重复创建 `Room A` 应显示 `room already exists`。

## AC-04 Web UI 创建、查询和取消预约

给定已有 `Room A`，当通过 `POST /ui/bookings` 表单提交：

| 字段 | 值 |
| --- | --- |
| `room_id` | `Room A` 的 ID |
| `user` | `Ada` |
| `title` | `Weekly Sync` |
| `start` | `2026-06-10 09:00` |
| `end` | `2026-06-10 10:00` |

则：

- HTML 正文包含 `Weekly Sync`、`Ada`、`2026-06-10 09:00`。
- `GET /ui/bookings?date=2026-06-10&room_id=<id>` 能看到该预约。
- `POST /ui/bookings/<id>/cancel` 后，该预约不再出现在查询结果中。
- 取消后的同一时间段可以重新预约。

## AC-05 JSON API 基础行为

`GET /health` 返回：

```json
{"status": "ok"}
```

`POST /rooms` 创建会议室后：

- 状态码 201。
- 响应包含 `id`、`name`、`capacity`、`location`。
- `GET /rooms` 能看到该会议室。

## AC-06 JSON API 预约和冲突检测

给定已有 Room A：

- `POST /bookings` 创建 `09:00-10:00` 返回 201，`status` 为 `active`。
- 同会议室 `09:30-10:30` 返回 409，JSON error 为 `booking conflict`。
- 同会议室 `10:00-11:00` 返回 201，边界相接不算冲突。
- 另一个会议室同一时间段允许预约。
- `GET /bookings?date=2026-06-10&room_id=<id>` 只返回该会议室当天 active 预约，并按 start 升序。

## AC-07 JSON API 取消预约

`DELETE /bookings/<id>`：

- 首次取消返回 204。
- 取消后查询列表不再包含该预约。
- 再次取消同一预约返回 404，JSON error 为 `booking not found`。
- 取消后同一时间段可以重新预约。

## AC-08 SQLite 持久化

使用同一个 SQLite 文件重新创建 app 后：

- 已创建会议室仍可通过 `GET /rooms` 查询。
- 未取消的 active 预约仍可通过 `GET /bookings` 查询。
- 已取消预约不出现在 active 查询结果中。

## AC-09 错误处理

以下错误必须返回错误状态码，并在 JSON 或 HTML 正文中包含稳定错误短语：

| 场景 | 稳定错误短语 |
| --- | --- |
| 空会议室名称、空预约人、空标题或缺字段 | `missing field` |
| capacity 小于等于 0 或不是整数 | `invalid capacity` |
| room_id 不存在 | `room not found` |
| start/end 格式错误或 start >= end | `invalid time` |
| 同会议室 active 预约重叠 | `booking conflict` |
| 预约不存在或已取消 | `booking not found` |

错误响应不能泄漏 Python traceback。
