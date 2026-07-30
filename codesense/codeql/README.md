# CodeQL code database builder

该目录提供一条不依赖 Java LSP 的离线构建链路：

```text
source repository
  -> CodeQL database create
  -> 5 bulk CodeQL queries
  -> BQRS decode to CSV
  -> indexed linear-time transform
  -> CodeSearch-compatible SQLite
```

默认输出 `codegraph.codeql.sqlite`，不会覆盖现有 LSP 链路生成的
`codegraph.sqlite`。SQLite 使用同一个 `init.code_db.CodeDatabase` schema，因此
可以直接对比以下六张表：

- `code_files`
- `code_symbols`
- `code_dependencies`
- `unresolved_calls`
- `code_edges`
- `code_implementations`

当前第一版针对 Java 项目，覆盖源码文件、class/interface/enum、method/constructor、
field、import、静态解析的调用目标、类型继承/实现和方法 override。CodeQL 调用查询
一次返回整个项目的调用 tuple，不会像 LSP call hierarchy 那样逐 method 发请求。

## 安装 CodeQL

请下载 GitHub 官方发布的完整 CodeQL bundle，而不是只下载 CLI 二进制。Bundle
同时包含 CLI、Java extractor 和 `codeql/java-all` query pack：

1. 打开 <https://github.com/github/codeql-action/releases>。
2. 下载最新版本的 `codeql-bundle-osx64.tar.gz`。
3. Apple Silicon Mac 如尚未安装 Rosetta，先执行：

   ```bash
   softwareupdate --install-rosetta --agree-to-license
   ```

4. 解压并加入 PATH，例如：

   ```bash
   mkdir -p "$HOME/tools"
   tar -xzf codeql-bundle-osx64.tar.gz -C "$HOME/tools"
   export PATH="$HOME/tools/codeql:$PATH"
   codeql version
   codeql resolve packs
   codeql resolve languages
   ```

建议把 `export PATH=...` 写入所使用 shell 的启动文件。CodeQL 的可用仓库范围和
授权条件以 GitHub 的
[CodeQL CLI 文档](https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-cli)
为准。

## 构建

先激活项目要求的 conda 环境：

```bash
conda activate codesearch
python -m codeQL.build_code_db \
  --project-path /path/to/java/repository \
  --output-dir /path/to/codesearch/output/project
```

默认使用 `--build-mode=none`，无需编译即可分析 Java；如果项目包含 Kotlin，
或希望让 CodeQL 使用完整的编译类型信息，建议使用 autobuild：

```bash
python -m codeQL.build_code_db \
  --project-path /path/to/repository \
  --output-dir /path/to/output \
  --build-mode autobuild
```

非标准构建可以显式指定命令：

```bash
python -m codeQL.build_code_db \
  --project-path /path/to/repository \
  --output-dir /path/to/output \
  --build-mode manual \
  --build-command "./mvnw -DskipTests package"
```

如果 `codeql` 没有加入 PATH，可以传：

```bash
--codeql-bin "$HOME/tools/codeql/codeql"
```

第一次运行会产生：

```text
codeql-database-java/          # 可复用的 CodeQL database
codeql-query-results/          # 解码后的批量查询结果
codegraph.codeql.sqlite        # 与 init schema 兼容的数据库
symbols_index.codeql.json
dependency_graph.codeql.json
codeql_build_summary.json      # 数量与各阶段耗时
```

修改转换逻辑后，无需重新抽取源码，可以复用 CodeQL database：

```bash
python -m codeQL.build_code_db \
  --project-path /path/to/repository \
  --output-dir /path/to/output \
  --reuse-codeql-db
```

`--threads 0` 表示 CodeQL 使用所有可用 CPU 核。内存受限时可同时指定
`--ram-mb 8192`。

如果自定义 query pack 提示依赖未解析，可在仓库根目录执行：

```bash
codeql pack install --mode=no-lock codesense/codeql/queries/java
```

完整 bundle 通常已经提供 `codeql/java-all`，因此这一步不应下载另一套不兼容的
Java library pack。

## 对比边界

- CodeQL `code_edges` 是静态解析后的调用目标；LSP 版本来自 call hierarchy。
- CodeQL 把所有调用点写入 `unresolved_calls`，能映射到项目源码 symbol 的调用
  同时写入 `code_edges`。
- CodeQL `code_implementations` 包含直接类型继承/实现和方法 override；LSP
  `textDocument/implementation` 的返回范围可能不同。
- `build-mode=none` 不处理 Kotlin 源码；包含 Kotlin 时使用 `autobuild` 或手工
  build command。
- CodeQL 的源码位置按 1 开始计数；转换层把列号改为现有数据库使用的 0-based。
