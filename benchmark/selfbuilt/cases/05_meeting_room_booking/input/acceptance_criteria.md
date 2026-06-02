# 会议室预约系统验收标准

## AC-01 健康检查

`GET /health` 返回：

- 状态码 200。
- JSON `{"status": "ok"}`。

## AC-02 创建和查看会议室

`POST /rooms` 创建会议室后：

- 状态码 201。
- 响应包含 `id`、`name`、`capacity`、`location`。
- `GET /rooms` 能看到该会议室。
- 重复名称返回 409。

## AC-03 创建预约

给定已有会议室，`POST /bookings` 创建预约：

- 状态码 201。
- 响应 status 为 `active`。
- 响应包含正确的 start 和 end。

## AC-04 冲突检测

已有 Room A 的预约 `09:00-10:00`：

- 再创建 `09:30-10:30` 返回 409。
- 创建 `10:00-11:00` 返回 201。
- 另一个会议室同一时间段允许预约。

## AC-05 查询预约

- `GET /bookings?date=2026-06-10` 只返回当天 active 预约。
- `GET /bookings?room_id=1` 只返回该会议室 active 预约。
- 结果按 start 升序。

## AC-06 取消预约

`DELETE /bookings/<id>`：

- 首次取消返回 204。
- 取消后查询列表不再包含该预约。
- 再次取消同一预约返回 404。
- 取消后同一时间段可以重新预约。

## AC-07 参数校验

以下场景必须返回错误状态码和 JSON error：

- 空会议室名称。
- capacity 小于等于 0。
- 不存在 room_id。
- start 晚于或等于 end。
- 时间格式错误。
- 请求体不是 JSON。
