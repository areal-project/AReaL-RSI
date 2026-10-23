# VerilogEval v2：Docker 构建与 ARCA 验收

固定上游 commit `c498220d0a52248f8e3fdffe279075215bde2da6`，使用
`dataset_code-complete-iccad2023/` 的全部 156 题。镜像只包含工具和 ARCA 运行组件，
不包含题目、测试台、参考实现、模型 SDK 或许可证服务。

## 构建

在能访问 ARCA 办公网 OSS 的机器准备构建上下文。脚本逐项核对 ARCA 组件的
SHA-256；`latest/arca_envd` 内容变化时会报错，需要显式审核更新哈希。

```bash
python3 examples/verilog_design/scripts/prepare_verilogeval_build.py \
  --output /tmp/verilogeval-context
```

把整个上下文传到能够访问 Docker Hub、Debian 软件源和 GitHub 的 Linux x86_64
构建机。构建机不必访问 ARCA 组件的 OSS 地址。传输中断可使用 `rsync --partial`
续传，核对文件 SHA-256 后再解包和构建。

```bash
docker build --platform linux/amd64 --progress plain \
  --iidfile image.id -t verilogeval:v2-c498220 \
  /tmp/verilogeval-context > build.log 2>&1
```

Dockerfile 固定 Debian 12 amd64 基础镜像 manifest digest，并从固定源码归档编译
Icarus Verilog 12.0。构建期间检查 Debian 12、Python 3.11 和 Verilator 5.006；
APT 包版本、编译日志和工具版本保存在镜像的 `/opt/provenance/`。
`image.id` 是本地 image config digest，不能当成仓库 manifest digest。

## 数据与 Docker 实测

```bash
python3 examples/verilog_design/scripts/fetch_verilogeval.py --output /tmp/verilogeval-input
cd /tmp/verilogeval-input/data
sha256sum -c SHA256SUMS
```

导出保留上游 LICENSE，校验四类文件各 156 个，生成逐文件大小和 SHA-256 清单。
已有上游 Git checkout 时可传 `--source /path/to/verilog-eval`；脚本直接归档固定
commit，不改变 checkout 或已有数据。

提交端需要 Python 3.11+ 和 PyYAML；镜像已经包含它们。以下命令在 Docker 主机
运行，数据和输出路径均使用主机真实路径：

```bash
python3 examples/verilog_design/scripts/verilogeval_golden.py \
  --dataset /tmp/verilogeval-input/data/dataset_code-complete-iccad2023 \
  --output /tmp/verilogeval-docker-results \
  --runtime docker --image verilogeval:v2-c498220 --jobs 4
```

脚本先检查 `Prob009_popcount3`，再检查全部参考实现，最后执行两项负向检查。
输出目录必须是新目录。每题只在工作副本中把 `RefModule` 替换为 `TopModule`；
原始参考实现与官方测试台保留。编译和仿真分别限时 30 秒，超时杀死进程组，
Docker 模式另外强制删除对应容器。

通过需要编译成功、仿真正常退出，以及整行 `Mismatches: 0 in <数字> samples`。
`results.jsonl` 和 `summary.json` 保留所有失败题，区分 `compile_error`、`fail`、
`timeout` 和环境 `error`，只有 156/156 通过且两项负向检查符合预期才返回零。

## 推送与注册

本次使用 `arcaarealagent` 租户、`stable` 环境和 `offline` 镜像运行态。
环境和运行态是不同配置项；线下仓库为 `antregistry-pre.antgroup-inc.cn`。
登录使用 ARCA 镜像管理页面设置的镜像中心账号密码，不使用 ARCA API Key。

```bash
docker login antregistry-pre.antgroup-inc.cn
image=antregistry-pre.antgroup-inc.cn/arcaarealagent/verilogeval-v2:UNIQUE_TAG
docker tag verilogeval:v2-c498220 "$image"
docker push "$image" > push.log 2>&1
docker image inspect "$image" --format '{{json .RepoDigests}}'
```

每次构建使用新 tag。记录 push 返回的 manifest digest，后续运行使用
`antregistry-pre.antgroup-inc.cn/arcaarealagent/verilogeval-v2@sha256:...`。
登录成功不代表拥有租户命名空间推送权限。已注册 ARCA Docker 账号后，租户管理员
可以通过 `POST /arca/openapi/docker/account/auth` 授权，body 包含
`tenantId` 和域账号 `loginName`；该接口针对 `arca_<loginName>`。

`scripts/arca.py` 的控制面配置见同目录 `arca-verilogeval.json`。
凭据通过 `ARCA_API_KEY` 环境变量，或不纳入 Git 的 `--credentials-file` JSON
文件中的 `api_key` 字段传入。不要把凭据放进请求记录或构建上下文。

```bash
python3 examples/verilog_design/scripts/arca.py \
  --config examples/verilog_design/containers/arca-verilogeval.json \
  --output image-status.json request POST /arca/openapi/images/query \
  --body-file image-query.json
```

`image-query.json`：

```json
{
  "tenantId": "arcaarealagent",
  "searchKey": "verilogeval-v2",
  "runtimeType": "offline",
  "currentPage": 1,
  "pageSize": 20
}
```

等待镜像状态为 READY、元数据完整后再创建沙箱。若通过 `/images/sync` 导入，
提交返回 SUCCESS 仅代表任务已提交，仍要轮询 `/images/query`。保存同步任务 ID、
镜像 ID、镜像地址和不可变 digest。

ARCA 的 Nydus 转换可能生成新的 manifest digest。最终运行使用 READY 记录的
`sha`，同时保留 push digest 和转换后 manifest 中的来源标注，不能混淆两个版本。

## ARCA 实际运行

使用已有 cleanroom 模板 `ARCA-TEMPLATE-0000000028cba9db`，覆盖为本次镜像
digest，分配 4 CPU、8 GB 内存、50 GB 磁盘和 60 分钟 TTL。通过
`POST /arca/openapi/v1/sandbox/instances` 创建，保存请求和响应。

运行时分别挂载：

| 内容 | Judge 挂载位置 | 权限 |
| --- | --- | --- |
| prompt、ifc、LICENSE 和清单 | `/mnt/prompts` | READ_ONLY |
| 官方 test、ref、LICENSE 和清单 | `/mnt/judge` | READ_ONLY |
| 独立输出工作区 | `/workspace` | READ_WRITE |

挂载 ID 从租户已有 OSS 配置取得，远端目录使用本次运行独立前缀。
通过 `arca.py upload` 上传 harness，通过实际终端接口
`POST /arca/api/v1/sandbox/<sandbox-id>/terminal/exec_command` 执行。
Judge 内使用 `verilogeval_golden.py --runtime native`；此时进程本身已经运行在
指定 Docker 镜像的 ARCA 容器中，不依赖 Apptainer 或嵌套 Docker。

本次锁定的 ENVD 不支持 `async_mode`。交付记录中的 `launch-judge.py` 通过
`subprocess.Popen(start_new_session=True)` 启动限时后台任务，标准输入输出均显式
重定向；终端接口返回启动回执后，通过 `driver.exit` 和日志查询最终状态。
不能将启动接口返回零当成验收通过。

运行前验证挂载只读、数据文件哈希和工具版本。结果、编译日志、仿真日志与
`/opt/provenance/` 一起归档到结果存储，通过 `arca.py download` 下载。
保存终端 command ID、sandbox ID、请求配置和响应。取回证据后调用
`DELETE /arca/openapi/v1/sandbox/instances/<sandbox-id>` 释放沙箱。

后续答题 Agent 只挂载题目和自己的独立工作区；不能挂载 Judge 数据、Judge 结果
目录或 Docker socket。Agent 退出并冻结提交后，再创建独立 Judge 容器。

## 2026-10-23 实际验收结果

本地 Docker 和最终 ARCA 镜像均为 156/156 通过；非法 Verilog 被识别为编译失败，
恒零输出被识别为测试失败（197/220 样本不匹配，仿真退出码仍为零）。只读挂载、
Judge 数据隔离与 Docker socket 隔离检查通过。5 个临时沙箱均已销毁。

最终镜像：

```text
antregistry-pre.antgroup-inc.cn/arcaarealagent/verilogeval-v2@sha256:d47edb8757b13f19dff6f5d735e84632e14eda5c0f3a384f60f23cd74d5848e9
```

- 镜像 ID：`119973`；同步转换任务：`55900002`。
- Judge 沙箱：`ARCA-SANDBOX-edba4912-efb4-4d75-9a01-6e3980d1c67d`。
- 实际启动 command ID：`verilogeval-final-golden-launch-20261023`。
- 全部记录及报告：`examples/verilog_design/runs/arca-verilogeval-20261022T120802Z/`。

首次 ACI 构建因访问 Docker Hub 超时失败，随后在指定构建机成功构建并上传。
构建机回拉转换后的镜像时受到办公网 OSS bucket policy 限制；ARCA 内部拉取和
运行该最终 digest 已实际验证。交付包保留两次平台的逐题日志和完整 Docker 构建日志。
