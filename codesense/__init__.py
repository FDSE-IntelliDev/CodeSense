"""CodeSense —— 把一条自然语言查询编译成一段针对代码库的查询脚本。

    自然语言 query  ──编译──▶  QL 脚本（Python）  ──执行──▶  带证据的结果

实现全部在 `codesense.ql`：脚本由若干基本查询算子编排，
所有算子都是 ``Frag -> Frag``。设计见 ``docs/design/``。

重写前那套 SemCon → SemQL → 三执行器的实现已归档到仓库根目录的
``legacy/``，不参与构建、lint 与测试。
"""

__version__ = "0.2.0.dev0"
