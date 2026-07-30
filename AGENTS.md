# AGENTS.md

给在这个仓库里干活的 agent 的约定。结构规则见
[ARCHITECTURE.md](ARCHITECTURE.md)，提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 环境

1. 运行代码前先激活 conda 环境 `codesearch`。
2. LLM key 从环境变量 `CODESENSE_API_KEY` 读，**不要写进代码或配置文件**。

## 写代码

3. 所有设计都要考虑算法性能，尽量降低时间复杂度，减少不必要的循环和判断分支。
4. 函数及函数的关键步骤写好注释。
5. 参数进 `configs/*.yaml`，通过 `load_config()` 取；不要在代码里写死路径、
   阈值、模型名，尤其不要写 `/Users/xxx/` 这种只在一台机器上成立的绝对路径。
6. 新代码放进 `codesense/` 对应的子包；入口脚本放 `scripts/`，且**不写业务逻辑**。
7. 模块顶层只有定义，不执行逻辑——`import` 一个模块不该读盘、起进程或 print。

## 提交前

8. 三条要过：`ruff check .`、`ruff format --check .`、`pytest`。
9. 只有在**完整实现一个功能、或确定一个功能的实现方案之后**才记录
   `CHANGELOG.md`，不用每做一次细节修改就同步。
10. 不要提交密钥、`output/` 产物、`slides/` 素材、模型权重。
