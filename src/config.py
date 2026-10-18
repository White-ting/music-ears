"""本机设置：~/.config/music-ears/config.json。没有这个文件、或者只写了一部分，其余用下面的默认。

  {
    "name": "小明",                       自己唱的、自己发的录音，乐谱卡上怎么称呼你（默认"用户"）
    "docker": "docker",                   docker 命令（不在 PATH 里就写完整路径）
    "tz": "Asia/Shanghai",                记录时间用的时区（不写就用系统时区）
    "models": {                           只写要改的那几项就行
      "listen": {"key_file": "...", ...},
      "review": {...},
      "asr":    {...}
    }
  }

钥匙放在单独的文件里（只写一行钥匙），key_file 指过去，别把钥匙直接写进 config.json。
"""
import json
import os

CONF_FILE = os.path.expanduser("~/.config/music-ears/config.json")

DEFAULT = {
    "name": "用户",
    "docker": "docker",
    "tz": "",
    "models": {
        # 听：要能输入音频的模型，OpenAI 兼容格式。默认阿里云百炼的通义千问 Omni
        "listen": {"proto": "openai", "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                   "model": "qwen3.8-omni-flash", "key_file": "~/.config/music-ears/keys/dashscope.key",
                   "audio_prefix": "data:;base64,"},
        # 审查：换一家模型逐段重听核对，Gemini 原生格式（有些中转的 OpenAI 格式会把音频丢掉）
        "review": {"proto": "gemini", "base": "https://generativelanguage.googleapis.com/v1beta",
                   "model": "gemini-3-flash", "key_file": "~/.config/music-ears/keys/gemini.key"},
        # 认字：听自己唱的录音时，认出唱的是哪几句（--sung 才用）
        "asr": {"base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3-asr-flash",
                "key_file": "~/.config/music-ears/keys/dashscope.key"},
    },
}


def load():
    c = json.loads(json.dumps(DEFAULT))
    if os.path.exists(CONF_FILE):
        mine = json.load(open(CONF_FILE, encoding="utf-8"))
        for k, v in mine.items():
            if k == "models" and isinstance(v, dict):
                for name, m in v.items():
                    c["models"].setdefault(name, {}).update(m or {})
            else:
                c[k] = v
    c["docker"] = os.path.expanduser(c["docker"])
    return c


def model(name):
    """某个模型的设置，带上读出来的钥匙。钥匙文件不在时说清楚去哪放。"""
    m = dict(load()["models"][name])
    path = os.path.expanduser(m["key_file"])
    if not os.path.exists(path):
        raise SystemExit("缺 %s 模型的钥匙：把钥匙写进 %s（或在 %s 的 models.%s.key_file 改路径）" % (name, path, CONF_FILE, name))
    m["key"] = open(path, encoding="utf-8").read().split()[0].strip()
    return m


def name():
    return load()["name"] or "用户"
