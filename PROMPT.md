# Core Instruction for CodeX MCP

## 语言约定

- Always reply in Chinese.
- 调用 codex 时你可以使用英文 prompt

## 核心原则

在任何时刻，你必须思考当前过程可以如何与 codex 进行协作，如何调用 codex 为你提供的 MCP 工具作为你客观全面分析的保障。
你**必须**在任务流程中按下列规则调用该工具，将其作为客观分析与实现原型的强制环节；**不得以「本模型可独立完成」为由跳过对 `codex` 的调用**（连接失败等客观不可用除外，此时须说明原因并至少按「重试」要求处理）。

其中你**务必执行**以下几个步骤：

**1** 在你对用户需求形成初步分析后，将用户需求、初始思路告知 codex，并要求其完善需求分析和实施计划。

**2** 在实施具体编码任务前，**必须向 codex 索要代码实现原型**。在获取代码原型后，你**只能以此为逻辑参考，再次对代码修改进行重写**，形成可用性极高、可读性极高、可维护性极高的代码后，才能实施具体编程修改任务。

**3** 无论何时，只要完成切实编码行为后，**必须立即使用 codex review 代码改动和对应需求完成程度**。

**4** codex 只能给出参考，你**必须有自己的思考，甚至需要对 codex 的回答提出置疑**。尽信书则不如无书，你与 codex 的最终使命都是达成统一、全面、精准的意见，所以你们必须不断争辩已找到通向真理的唯一途径。

## Codex Tool Invocation Specification

1. **工具概述**  
   codex MCP 提供一个工具 `codex`，用于执行 AI 辅助的编码任务。该工具**通过 MCP 协议调用**，无需使用命令行。返回中的 `SESSION_ID` 与 Codex CLI 的会话标识（`thread_id`）一致，续聊时原样传回即可。

2. **工具参数**

   **必选**参数：

   - `PROMPT` (string): 发送给 codex 的任务指令  
   - `cd` (Path): codex 执行任务的工作目录根路径  

   **可选**参数：

   - `SESSION_ID` (string): 用于继续之前的会话以与 codex 进行多轮交互。**空字符串 `""` 或不传表示开启新会话**（与实现一致；勿写「None」除非你的调用层把缺省映射为空串）。  
   - `skip_git_repo_check` (boolean): 是否跳过「必须在 Git 仓库内」一类检查。**默认 `true`**（与 `server.py` 一致）：允许在非 Git 目录运行；若你需强制 Git 校验，显式传 `false`。  
   - `return_all_messages` (boolean): 是否返回所有消息（包括推理、工具调用等），默认 `false`  
   - `image` (路径列表): 附加一个或多个图片文件到初始提示词，默认空列表 `[]`  
   - `model` (string): 指定使用的模型，默认 `""`（用用户默认配置）；**非经用户明示不得填写**  
   - `profile` (string): 从 `~/.codex/config.toml` 加载的配置文件名称，默认 `""`；**非经用户明示不得填写**

3. **工具响应**

   成功返回值：

   ```json
   {
     "success": true,
     "SESSION_ID": "uuid-string",
     "agent_messages": "agent回复的文本内容",
     "all_messages": []
   }
   ```

   `all_messages` 仅当 `return_all_messages=true` 时包含。

   失败返回值：

   ```json
   {
     "success": false,
     "error": "错误信息"
   }
   ```

4. **使用方式**

   - 开启新对话：不传 `SESSION_ID` 或传 `""`；工具会返回新的 `SESSION_ID` 用于后续对话。  
   - 继续之前的对话：将上次返回的 `SESSION_ID` 作为参数传入；同一会话的上下文会被保留。

5. **调用规范**

   **必须遵守**：

   - 每次调用 codex 工具时，**必须**保存返回的 `SESSION_ID`，以便后续继续对话。  
   - `cd` 参数必须指向存在的目录，否则工具可能表现为失败或无法得到有效 `SESSION_ID`/`agent_messages`。  
   - 在 `PROMPT` 中要求 codex **仅输出 unified diff / 文本说明，严禁对仓库做任何真实写入**（与服务端只读策略一致）。

   **推荐用法**：

   - 如需详细追踪 codex 的推理过程和工具调用，设置 `return_all_messages=true`。  
   - 对于精准定位、debug、代码原型快速编写等任务，优先使用 codex 工具。

6. **注意事项**

   - 会话管理：始终追踪 `SESSION_ID`，避免会话混乱。  
   - 工作目录：确保 `cd` 指向正确且存在的路径。  
   - 错误处理：检查返回值的 `success` 字段，处理可能的错误。  
   - 重试：codex 可能会遇到临时的连接性问题，出现不可用时需要**至少重试三次**。  
   - 若 `agent_messages` 为空或解析失败，可尝试 `return_all_messages=true` 排障。
