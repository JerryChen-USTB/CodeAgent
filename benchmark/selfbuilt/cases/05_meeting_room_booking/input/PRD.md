# 会议室预约系统 PRD

## 1. 产品概述

会议室预约系统是一款面向小团队的本地 Flask Web 应用。行政管理员可以在浏览器中维护会议室，员工可以创建和取消会议室预约，团队成员可以查看某天或某个会议室的占用情况。系统同时提供 JSON API，便于自动化集成和隐藏 oracle 验收。

本案例是第四个“图书借阅 Web UI”案例的升级版：第四案要求使用 Python 标准库实现本地 Web UI；本案要求使用 Flask 实现浏览器 Web UI，并保留后端 JSON API。最终成品不能只是纯 API，也不能只靠手动发送请求操作，演示时必须能在浏览器中完成核心预约流程。

## 2. 产品目标

- 使用 Flask 提供本地 Web UI 和 JSON API。
- 使用 SQLite 持久化会议室和预约数据。
- 通过 `create_app(db_path=None)` 暴露 Flask app factory，便于测试隔离。
- 支持浏览器页面创建会议室、创建预约、筛选预约、取消预约。
- 支持 JSON API 完成同样的核心业务，供自动化验收使用。
- 正确处理预约时间冲突，取消后释放时间段。
- Agent 需要创建 `workspace/requirements.txt`，声明 `Flask>=3.0,<4.0` 或兼容 Flask 3 的依赖。

## 3. 默认启动方式

用户在生成项目的 workspace 中运行：

```bash
python -m meeting_room_booking --db meeting_rooms.db --host 127.0.0.1 --port 8000
```

程序应启动 Flask 本地服务。浏览器访问：

```text
http://127.0.0.1:8000/
```

首页必须包含 `会议室预约系统`，并提供进入会议室管理和预约管理的入口。

## 4. 用户角色

| 角色 | 目标 |
| --- | --- |
| 行政管理员 | 创建会议室、查看会议室列表 |
| 普通员工 | 创建会议预约、取消自己不再需要的预约 |
| 团队成员 | 按日期和会议室查看 active 预约 |

本版本不做登录和权限控制，表单中的 `user` 字段代表预约人。

## 5. 核心业务规则

- 会议室名称唯一。
- 会议室 capacity 必须为正整数。
- 预约时间格式为本地时间字符串 `YYYY-MM-DD HH:MM`。
- 预约开始时间必须早于结束时间。
- 同一会议室 active 预约不能重叠。
- 时间边界相接不算冲突，例如 `09:00-10:00` 和 `10:00-11:00` 可以同时存在。
- 已取消预约不参与冲突检测，也不在查询列表中返回。
- 取消后的时间段可以重新预约。
- SQLite 数据库路径由 `create_app(db_path=...)` 或 CLI `--db` 注入。
- 每次数据库操作结束后必须关闭 SQLite 连接，避免 Windows 下临时数据库文件被占用。

## 6. Web UI 功能需求

### F-01 首页

`GET /` 返回 HTML 首页，至少包含：

- 标题 `会议室预约系统`
- 会议室管理入口
- 预约管理入口
- 创建会议室表单或入口
- 创建预约表单或入口
- 按日期和会议室筛选预约的入口

页面可以朴素，但必须能在浏览器中直接使用。

### F-02 会议室管理页面

`GET /ui/rooms` 返回会议室列表和创建会议室表单。页面应显示每个会议室的名称、容量和位置。

`POST /ui/rooms` 使用表单字段：

| 字段 | 规则 |
| --- | --- |
| `name` | 必填，非空，唯一 |
| `capacity` | 必填，正整数 |
| `location` | 可选，默认空字符串 |

成功后页面正文应包含新会议室名称，例如 `Room A`。重复名称应显示 `room already exists`，非法容量应显示 `invalid capacity`。

### F-03 预约管理页面

`GET /ui/bookings` 返回预约列表和创建预约表单，支持查询参数：

```text
date=2026-06-10
room_id=1
```

结果只显示 active 预约，按开始时间升序、再按 id 升序。

`POST /ui/bookings` 使用表单字段：

| 字段 | 规则 |
| --- | --- |
| `room_id` | 必填，必须存在 |
| `user` | 必填，非空 |
| `title` | 必填，非空 |
| `start` | 必填，格式 `YYYY-MM-DD HH:MM` |
| `end` | 必填，格式 `YYYY-MM-DD HH:MM` 且晚于 start |

成功后页面正文应包含预约标题、用户、开始时间和结束时间。会议室不存在显示 `room not found`，冲突显示 `booking conflict`，时间非法显示 `invalid time`。

### F-04 取消预约

`POST /ui/bookings/<id>/cancel` 取消 active 预约。取消成功后，该预约不再出现在 Web UI 查询结果中。不存在或已取消的预约显示 `booking not found`。

## 7. JSON API 需求

JSON API 保持稳定，用于自动化验收。

### 健康检查

```http
GET /health
```

返回：

```json
{"status": "ok"}
```

### 会议室

- `POST /rooms`：创建会议室，成功返回 201 和会议室 JSON。
- `GET /rooms`：返回会议室数组，按 id 升序。

错误：

- 参数错误：400，`{"error": "missing field"}` 或 `{"error": "invalid capacity"}`。
- 名称重复：409，`{"error": "room already exists"}`。

### 预约

- `POST /bookings`：创建预约，成功返回 201 和预约 JSON，`status` 为 `active`。
- `GET /bookings?date=2026-06-10&room_id=1`：返回 active 预约数组。
- `DELETE /bookings/<id>`：取消预约，成功返回 204，无响应体。

错误：

- 会议室不存在：404，`{"error": "room not found"}`。
- 时间冲突：409，`{"error": "booking conflict"}`。
- 时间非法：400，`{"error": "invalid time"}`。
- 预约不存在或已取消：404，`{"error": "booking not found"}`。

## 8. 数据模型

### Room

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 自动生成 |
| `name` | str | 会议室名称，唯一 |
| `capacity` | int | 容量，正整数 |
| `location` | str | 位置，可为空 |

### Booking

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 自动生成 |
| `room_id` | int | 会议室 ID |
| `user` | str | 预约人 |
| `title` | str | 会议标题 |
| `start` | str | 开始时间，`YYYY-MM-DD HH:MM` |
| `end` | str | 结束时间，`YYYY-MM-DD HH:MM` |
| `status` | str | `active` 或 `cancelled` |

## 9. 非目标

- 不做登录、权限和用户管理。
- 不做邮件通知。
- 不做周期性会议。
- 不做跨时区处理。
- 不要求复杂前端框架或构建工具。
- 不要求 REST API 以外的外部集成。
