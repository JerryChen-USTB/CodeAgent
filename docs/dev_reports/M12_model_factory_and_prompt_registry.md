# M12 Model Factory 与 Prompt Registry

## 目标

本里程碑实现模型接入和提示词集中管理的基础能力：安全解析 OpenRouter API Key、创建 OpenAI-compatible LangChain chat model、提供结构化输出校验重试工具，并维护各 Agent 角色的系统提示词。

## 主要变更

- `codeagent/models/secrets.py`：新增 `SecretResolver`、`ResolvedSecret` 和 `MissingModelSecretError`。
- `codeagent/models/factory.py`：新增 `ModelClientFactory`，将 `ModelConfig` 映射到 `langchain_openai.ChatOpenAI`。
- `codeagent/models/structured_outputs.py`：新增 `invoke_with_structured_retry()` 和结构化输出失败异常。
- `codeagent/agents/prompts.py`：新增 `PromptRegistry` 和 8 个默认角色提示词。
- `tests/unit/models/`、`tests/unit/agents/`：覆盖模型参数映射、缺失 key、脱敏记录、Prompt 规则和结构化输出重试。

## 官方文档与版本核对

- LangChain structured output 文档说明结构化输出可使用 Pydantic schema，并支持 provider/tool 策略和错误重试机制：https://docs.langchain.com/oss/python/langchain/structured-output
- OpenRouter quickstart/API 示例确认 OpenRouter 提供 OpenAI-compatible `https://openrouter.ai/api/v1/chat/completions` 和 Bearer API Key：https://openrouter.ai/docs/quickstart
- OpenRouter LangChain 集成文档当前推荐专用 `ChatOpenRouter` 包：https://openrouter.ai/docs/guides/community/langchain

本地依赖版本：

- `langchain==1.3.2`
- `langchain-openai==1.2.2`
- `langgraph==1.2.2`
- `openai==2.40.0`
- `pydantic==2.10.6`
- `langchain-openrouter` 未安装

因此本阶段遵循现有设计和依赖基线，使用 `ChatOpenAI(base_url="https://openrouter.ai/api/v1")`；后续如新增 `langchain-openrouter` 依赖，可替换为专用 `ChatOpenRouter`。

## 设计决策

- API Key 只从环境变量读取，错误信息只暴露 env var 名称；若 `api_key_env` 本身像 secret 或不是合法 env var 名称，则返回不含原值的错误。
- `ResolvedSecret.__repr__()` 和 `to_record()` 都脱敏，避免后续 metadata/report 误写密钥。
- PromptRegistry 的每个角色 prompt 都显式包含 Inputs、Allowed tools、Output schema、Verification、Failure behavior，并统一包含 patch-first、hidden oracle、no secret、schema、audit 规则。
- 结构化输出 helper 不直接绑定具体 LLM，接收 producer callable，便于后续 workflow 节点和测试注入。

## 验证

- `python -m pytest tests/unit/models tests/unit/agents -q`：10 passed。
- `python -m py_compile codeagent/models/__init__.py codeagent/models/secrets.py codeagent/models/factory.py codeagent/models/structured_outputs.py codeagent/agents/__init__.py codeagent/agents/prompts.py`：通过。
- `python -m pytest -q`：117 passed。
- `python -m codeagent --help`：退出码 0。
- `codeagent --help`：退出码 0。

## 复审结果

- Spec review：PASS。
- Quality review：初次发现 prompt 分段不足、secret-like `api_key_env` 可能泄漏；修复后 APPROVED。

## 限制与后续

- 当前未实际调用远程模型，避免在单元测试中依赖网络和真实 API Key。
- `ChatOpenRouter` 专用集成未纳入当前依赖，可在模型扩展里程碑或依赖更新时评估。
- Prompt 目前是集中字符串模板，后续 Agent 节点落地时应补充 schema snapshot 和角色输出样例。
