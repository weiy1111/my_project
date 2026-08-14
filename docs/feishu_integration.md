# 飞书推送集成指南

## 快速配置

### 1. 确认lark-cli已安装
```powershell
lark-cli --version
```

### 2. 确认认证状态
```powershell
lark-cli config show
```

### 3. 测试推送
```powershell
lark-cli im +messages-send \
  --user-id "ou_5ba3ae2c943ce54e8cb514eab9f8faf8" \
  --as bot \
  --text "测试消息"
```

---

## Captain使用方式

### 方式A：命令行直接推送

```powershell
# 生成报告后，通过管道推送
lark-cli im +messages-send \
  --user-id "ou_5ba3ae2c943ce54e8cb514eab9f8faf8" \
  --as bot \
  --markdown "## 测试报告

- 指数1: +1.2%
- 指数2: -0.5%
"
```

### 方式B：Python脚本推送

```python
import subprocess
import tempfile
from pathlib import Path

LARK_BIN = r"C:\Users\86182\AppData\Roaming\npm\lark-cli.cmd"
USER_ID = "ou_5ba3ae2c943ce54e8cb514eab9f8faf8"

def push_to_feishu(markdown: str) -> bool:
    """推送Markdown报告到飞书"""
    # 写入临时文件
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.md', delete=False, encoding='utf-8'
    ) as f:
        f.write(markdown)
        tmp = f.name
    
    # 执行推送
    cmd = [LARK_BIN, "im", "+messages-send",
           "--user-id", USER_ID, "--as", "bot",
           "--markdown", f"@{tmp}"]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    Path(tmp).unlink(missing_ok=True)
    
    return result.returncode == 0


# 使用示例
report = """# 大A‑Daily 每日复盘｜2026-08-08

## 一、大盘全景快照
- 上证指数: 3200.15 (+0.85%)
- 深证成指: 10500.32 (+1.20%)
"""

if push_to_feishu(report):
    print("推送成功")
else:
    print("推送失败")
```

---

## 关键参数说明

| 参数 | 说明 | 示例 |
|------|------|------|
| `--user-id` | 目标用户open_id | `ou_xxx` |
| `--chat-id` | 目标群聊ID | `oc_xxx` |
| `--as bot` | 以Bot身份发送 | - |
| `--as user` | 以用户身份发送 | - |
| `--markdown` | Markdown格式内容 | - |
| `--text` | 纯文本内容 | - |
| `--dry-run` | 预览不发送 | - |

---

## 常见问题

### Q1: lark-cli找不到
**原因**: npm全局目录不在PATH中
**解决**: 使用完整路径 `C:\Users\86182\AppData\Roaming\npm\lark-cli.cmd`

### Q2: 推送报错 `missing scope`
**原因**: 缺少权限
**解决**: 重新登录授权
```powershell
lark-cli auth login --scope "im:message.send_as_user" --no-wait --json
```

### Q3: Markdown格式不生效
**原因**: lark-cli使用post格式
**解决**: 使用 `--markdown` 参数，它会自动转换为飞书格式

### Q4: 报告太长推送失败
**原因**: 单条消息长度限制
**解决**: 分段推送，或使用 `--file` 发送Markdown文件

---

## 推荐配置

在项目根目录创建 `.env` 文件：
```env
# 飞书用户ID
FEISHU_USER_ID=ou_5ba3ae2c943ce54e8cb514eab9f8faf8

# 或群聊ID
FEISHU_CHAT_ID=oc_xxx
```

然后在脚本中读取：
```python
import os
from dotenv import load_dotenv

load_dotenv()
USER_ID = os.getenv("FEISHU_USER_ID", "ou_5ba3ae2c943ce54e8cb514eab9f8faf8")
```
