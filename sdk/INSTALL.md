# document-processing-sdk 1.8.0 安装说明

**复用已验证Wheel，与提交220a9ee一致。** 本次交付逐成员核对了完整SDK与当前提交，直接复用原字节；没有重新构建、重跑测试、重复安装验证或升级任何PJ。

| 项目 | 固定身份 |
| --- | --- |
| 完整Commit | 220a9ee228346d9b2cf9147966590808c10a4b39 |
| 包名／导入名 | document-processing-sdk / document_sdk |
| 版本 | 1.8.0 |
| Wheel文件名 | document_processing_sdk-1.8.0-py3-none-any.whl |
| Wheel SHA256 | d1dee21ff4b03c555cc1884270062bebc7267f14dec0ade287f5133fb3081ba0 |
| Requires-Python | >=3.12,<3.13，即Python 3.12 |

## 1. PJ自己的环境与包身份

每个PJ使用自己的Python 3.12 venv。必须安装本Wheel，不引用SDK仓库、不把仓库src加入PYTHONPATH、不使用editable或复制包目录。示例路径可含空格：

~~~bash
python3.12 -m venv "/path/to/your PJ/.venv"
PJ_PYTHON="/path/to/your PJ/.venv/bin/python"
SDK_WHEEL="/Users/kyoen/Projects/sdk/document-processing-sdk/1.8.0-220a9ee/document_processing_sdk-1.8.0-py3-none-any.whl"

shasum -a 256 "$SDK_WHEEL"
# 必须等于上表完整SHA；也可在配布目录校验Wheel和INSTALL.md：
(cd "$(dirname "$SDK_WHEEL")" && shasum -a 256 -c SHA256SUMS)
~~~

三个文件可以一起复制到其他位置，随后调整SDK_WHEEL。所有路径和带extras的完整参数都保持引号。目录中的220a9ee标识提交，不添加到标准Wheel文件名；同名1.8.0不代表同一个包。

以下命令供PJ负责人执行，本次交付没有执行客户安装或部署。

## 2. 安装所需能力

选择相应命令；pip按Wheel的METADATA/extras处理依赖：

~~~bash
# LOCAL PDF
"$PJ_PYTHON" -m pip install "${SDK_WHEEL}[pdf]"

# HYBRID PDF
"$PJ_PYTHON" -m pip install "${SDK_WHEEL}[pdf,azure-document-intelligence]"

# Excel
"$PJ_PYTHON" -m pip install "${SDK_WHEEL}[excel]"

# DOCX基础支持
"$PJ_PYTHON" -m pip install "$SDK_WHEEL"
~~~

DOCX不需要单独extra。基础METADATA依赖是pydantic>=2.10,<3及defusedxml>=0.7,<1，支持既有非宏DOCX的本地检查与结构分析，不包括Word排版渲染、宏执行或完整往返编辑。

同一完整Wheel也保留Image代码：需要图像功能时安装[image]，需要既有OpenCV功能时安装[image-opencv]。Excel按[excel]安装既有xlsx/xlsm能力。PDF安装不是裁剪为PDF专用包；[all]不代替明确PDF extras，尤其不包含高级PDF所需的全部专用解析依赖。

每次安装后执行依赖检查和基础导入路径检查：

~~~bash
"$PJ_PYTHON" -m pip check
"$PJ_PYTHON" -I -B -c 'import sys,document_sdk,importlib.metadata as m; print(sys.executable); print(sys.prefix); print(document_sdk.__file__); print(m.version("document-processing-sdk")); assert sys.prefix != sys.base_prefix; assert m.version("document-processing-sdk") == "1.8.0"'
~~~

安装PDF extra后，从PJ目录进一步核对实际模块来自PJ的venv并与指定Wheel一致；此命令只做导入/路径/字节检查，不解析PDF或调用Azure：

~~~bash
SDK_WHEEL="$SDK_WHEEL" "$PJ_PYTHON" -I -B - <<'PY'
import hashlib
import os
from pathlib import Path
import sys
import zipfile
import document_sdk
import document_sdk.pdf.local_first as local_first
import document_sdk.pdf.local_first_models as local_first_models

wheel = Path(os.environ["SDK_WHEEL"])
assert hashlib.sha256(wheel.read_bytes()).hexdigest() == "d1dee21ff4b03c555cc1884270062bebc7267f14dec0ade287f5133fb3081ba0"
assert sys.version_info[:2] == (3, 12) and sys.prefix != sys.base_prefix
assert document_sdk.__version__ == "1.8.0"
package_root = Path(document_sdk.__file__).resolve().parent
with zipfile.ZipFile(wheel) as archive:
    for module in (document_sdk, local_first, local_first_models):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(Path(sys.prefix).resolve())
        member = "document_sdk/" + path.relative_to(package_root).as_posix()
        assert path.read_bytes() == archive.read(member)
        print(module.__name__, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert (package_root / "py.typed").read_bytes() == archive.read("document_sdk/py.typed")
PY
~~~

仅安装基础DOCX或Excel的PJ先执行基础检查，PDF模块检查在安装相应PDF extra后执行。

离线PJ需要另备目标平台完整依赖包。例如LOCAL：

~~~bash
OFFLINE_WHEELS="/path/to/trusted offline wheels"
"$PJ_PYTHON" -m pip install --no-index --find-links "$OFFLINE_WHEELS" "${SDK_WHEEL}[pdf]"
"$PJ_PYTHON" -m pip check
~~~

HYBRID、Excel、DOCX使用相同离线选项，替换为各自安装参数。缺包时补齐可信离线依赖，不使用--no-deps、开发venv或源码绕过。

## 3. 最小LOCAL调用

~~~python
from pathlib import Path
from document_sdk.pdf import analyze_pdf_local_first

result = analyze_pdf_local_first(Path("input.pdf"))
print(result.status, result.origin)
~~~

默认LOCAL，执行原ADVANCED_LOCAL一次。SDK受控调用链不访问云Context、解析云凭据或发送云请求；LOCAL不要传Azure Provider或完整上传许可。输入为Path指向的常规PDF，既有安全、密码和有限资源约束生效。本入口不新增本地OCR选项，不下载OCR模型。

## 4. 客户Provider与HYBRID调用

PJ提供客户自己的Azure Document Intelligence资源、账号和订阅，使用现有公开Provider/Context/Runtime CredentialResolver。下面没有SANZO资源或真实密钥。

客户/PJ在其安全运行环境中注入CUSTOMER_AZURE_DI_ENDPOINT与CUSTOMER_AZURE_DI_KEY。代码只向resolver提供密钥环境变量名称；构造配置不解析密钥，真正进入已授权云调用时才由Provider运行时解析。不要把密钥写入本文件、模型、日志或Git；SDK不自动读取.env。

~~~python
import os

from document_sdk.core import CloudExecutionPolicy
from document_sdk.ocr import (
    AzureDocumentIntelligenceConfig,
    AzureDocumentIntelligenceProvider,
    AzureEnvironmentKeyCredentialResolver,
)
from document_sdk.providers import ProviderContext

config = AzureDocumentIntelligenceConfig(
    endpoint=os.environ["CUSTOMER_AZURE_DI_ENDPOINT"],  # 客户自己的HTTPS资源根地址
    model_id="prebuilt-layout",
    api_version="2024-11-30",
    max_input_bytes=50_000_000,
    polling_timeout_seconds=120,
    cloud_execution_policy=CloudExecutionPolicy(
        allow_cloud_execution=True,
        allow_cloud_fallback=True,
        max_logical_cloud_operations=1,
        max_submitted_operations=1,
        max_paid_or_possibly_paid_operations=1,
        max_pages_or_frames=20,
        max_input_bytes=50_000_000,
    ),
)
customer_provider = AzureDocumentIntelligenceProvider(
    context=ProviderContext(
        profile_id="customer-azure-di",
        config=config,
        credential_resolver=AzureEnvironmentKeyCredentialResolver(
            "CUSTOMER_AZURE_DI_KEY"
        ),
    )
)
~~~

页数/字节/操作预算是有限示例值，PJ按客户批准范围配置；更严格的本地及Provider限制仍生效。执行与fallback双许可不能代替完整上传授权。

只有客户明确授权本次完整原始PDF上传时，才把下例许可设为True。仅指定页上传许可不能扩大为整份PDF；选择HYBRID和提供Provider本身均不能代替具体上传授权：

~~~python
from pathlib import Path
from document_sdk.pdf import PdfLocalFirstMode, analyze_pdf_local_first

result = analyze_pdf_local_first(
    Path("input.pdf"),
    mode=PdfLocalFirstMode.HYBRID,
    azure_provider=customer_provider,
    allow_full_document_upload=True,
)
print(result.status, result.origin)
~~~

HYBRID仍先本地，本地充分时直接采用LOCAL，不访问未用云Context或凭据。只有批准的明确技术缺口（无文字层、已报告cell值缺失、分隔闭合结构不足）、允许伴随原因、安全/来源检查、完整上传许可及预算全部符合时，才至多一次整份原PDF上传并分析全部原页。不是任意异常都上云；Family固定REVIEW_REQUIRED、OCR_NOT_EXECUTED、普通PARTIAL或空文字本身不触发，未知warning、资源/安全/密码/来源问题和本地异常均不靠云绕过。

云被采用时最终文字、表格和页全部来自同一次云结果，不与Local混合。云调用、映射、缺页、来源或投影失败时FAILED、content=None、origin=None，不恢复已判不足的Local冒充成功，不自动重试；已有实际Usage/Attempt保留。未切云的合法不足可返回PARTIAL，无合法可用内容时FAILED。

普通PJ读取status、origin和content；failure_reason、provider_usage、cloud_attempt用于必要排查。SUCCESS表示这份有限处理合同完成，不是原文准确率100%、没有任何漏取或云一定更准确的认证。

## 5. 包含范围、验证限制与旧版本更新

本Wheel是完整SDK自身代码，保留core、files、PDF、OCR/providers/pipeline、Excel、DOCX、Image、document/batch、extraction/workflow及py.typed等当前模块。298个SDK成员与本Commit一致，未携带客户凭据文件、.env、测试语料或开发环境文件。

Wheel不捆绑Python、全部第三方依赖或OCR模型，依赖由安装器按METADATA/extras处理。SDK Wheel的py3-none-any标签不保证第三方运行库跨平台通用，也不意味着单个Wheel可离线装齐一切。离线客户另备目标系统/架构/Python对应的依赖包；本次没有制作跨平台离线依赖合集。

已有工程与独立安装验收针对本SHA。实际独立安装覆盖macOS arm64 / Python 3.12.13、[pdf] LOCAL及添加Azure extra后的Mock HYBRID；不宣称Linux、Windows、其他Python版本、真实Azure识别质量或生产性能已验证。本轮仅核对并复用包，没有重复这些验收。

已有PJ装过旧1.8.0时，应由该PJ在自己的授权部署流程中重新安装这个指定SHA的Wheel，例如LOCAL：

~~~bash
"$PJ_PYTHON" -m pip install --force-reinstall "${SDK_WHEEL}[pdf]"
"$PJ_PYTHON" -m pip check
~~~

HYBRID使用[pdf,azure-document-intelligence]，离线时同时加--no-index与--find-links。之后再次核对导入路径、版本及上述模块字节，并按PJ运行方式重启进程。同名/同版本不能替代SHA身份；只替换共享Wheel不会更新已安装的site-packages。本次没有替任何PJ安装、升级、重启或发布。
