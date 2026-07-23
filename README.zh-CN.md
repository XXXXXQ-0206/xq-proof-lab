# XQ Proof Lab

[English](README.md) | 简体中文

XQ Proof Lab 是一个本地、无第三方运行时依赖的 Python 工具集，用于重放
中国象棋局面，运行有界 AND/OR 证明搜索，验证证明证书，将已验证工件保存
到 SQLite，并通过 UCI 提供本地分析。

它是规则与分析项目，不是棋力声明。项目不捆绑 Pikafish、NNUE 权重、在线
数据或训练基础设施。

## 项目概览

项目将证明结论与普通分析严格分开。标记为 `proof` 或 `proof_store` 的结果
必须拥有可在本地重放并被独立验证器接受的工件。`self_fallback` 只是本地
分析；外部引擎、NNUE 和网络服务是可选诊断输入，不能证明节点。

## 功能

- 中国象棋 FEN 解析、UCI `position` 历史重放、合法走法生成、将军检测和
  保守的重复判定。
- 有界 proof-number、DFPN 和 AND/OR 搜索组件。
- 独立证书验证和紧凑证明证书。
- 带工件哈希和历史敏感键的本地 SQLite 证明存储。
- 对合法局面返回一个合法 `bestmove` 的 UCI 循环。
- 规则探针、perft、报告校验器和诊断性 UCI 对局工具。

## 截图

XQ Proof Lab 是命令行和库项目，没有图形界面，也没有截图集。

## 安装

当前记录的轻量验证环境是 Windows 上的 CPython 3.14。源码语法目标为
Python 3.10 或更高版本，但其他 Python 版本和平台尚未完成发布级验证。

```powershell
git clone https://github.com/XXXXXQ-0206/xq-proof-lab.git
cd xq-proof-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --editable .
```

支持的本地工作流只使用 Python 标准库。规则、证明、证书、存储和本地 UCI
工作流不需要引擎、网络服务或下载的模型。

## 使用

运行本地 perft 检查：

```powershell
python .\tools\perft.py --depth 1
```

标准初始局面在深度 1 应返回 `44` 个节点。

启动封闭的本地 UCI 适配器：

```powershell
python .\tools\proof_uci.py --closed
```

然后发送标准 UCI 会话，例如：

```text
uci
isready
position startpos
go depth 1
quit
```

运行搜索前请先对每个工具使用 `--help`。长时间证明搜索、对局批次、外部
引擎比较、下载和网络查询属于可选研究或诊断工作，不属于发布验证。

## 构建说明

项目没有原生编译步骤。`pyproject.toml` 会打包 `src/` 下的三个 Python
库；`pip install --editable .` 是开发安装方式。命令行工具保留在 `tools/`
中，使调用方式和生成文件路径保持明确。

## 验证

在仓库根目录运行以下轻量检查：

```powershell
python -m compileall -q src tools tests
python -m unittest discover -s tests -v
python .\tools\perft.py --depth 1
python .\tools\proof_uci.py --help
git diff --check
```

这些命令验证功能回归和工具契约，不是性能基准或棋力评估。

## 项目结构

```text
src/xiangqi_core/        规则、局面、走法生成和对局历史
src/xiangqi_solver/      证明搜索、证书、验证器和 SQLite 存储
src/xiangqi_evaluators/  本地及可选诊断走法排序适配器
tools/                   明确的命令行工作流
tests/                   unittest 回归测试
configs/                 版本化示例和可再生产物清单
docs/                    规则、维护、证据和项目边界文档
```

## 路线图

近期维护重点是规则回归、UCI 生命周期覆盖、工件/报告一致性和有界搜索
正确性。可选研究工作单独记录，并且必须先修复历史 A/B 计时语义，再生成
新的资格数据。详见 [docs/ROADMAP.md](docs/ROADMAP.md) 和
[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)。

## 贡献

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。贡献通过 Pull Request 进入；
不要提交外部二进制文件、NNUE 权重、本地 SQLite、实验工件、凭据或用户特定
设置。

## 许可证

XQ Proof Lab 使用 [MIT License](LICENSE) 发布。第三方工具和资源不随项目
捆绑，并继续受其自身条款约束。

## 常见问题

**仓库是否包含 Pikafish 或 NNUE 文件？**

不包含。它们是可选的本地诊断资源，并由 Git 忽略。

**返回合法 `bestmove` 是否意味着局面已被证明？**

不是。只有经本地验证的证明工件才能支持证明结论。

**项目是否声称战胜其他引擎？**

不声称。项目不作棋力或 Elo 声明。

## 致谢

Pikafish 兼容性说明和可选诊断脚本引用上游
[official-pikafish/Pikafish](https://github.com/official-pikafish/Pikafish) 项目。
XQ Proof Lab 不重新发布 Pikafish、NNUE 文件、ChessDB 响应或其他外部资源。
详见 [docs/THIRD_PARTY_AND_EVIDENCE.md](docs/THIRD_PARTY_AND_EVIDENCE.md)。

## 免责声明

本软件按“现状”（AS IS）提供，不提供任何明示或默示保证。作者和贡献者不对
因使用本软件产生的任何直接、间接、附带、特殊、示范性或后果性损害承担责任。
用户自行承担使用本软件及其分析结果的全部风险。

不得将本项目用于违法、侵权、欺骗、作弊、未经授权访问或规避规则的活动。用户
负责取得必要授权，并遵守适用法律、平台规则及第三方许可证。
