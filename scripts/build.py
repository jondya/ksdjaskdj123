#!/usr/bin/env python3
import os, sys, json, subprocess, urllib.request
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules"
OUT_CLASH = ROOT / "out/clash"
OUT_SBOX = ROOT / "out/singbox"
OUT_SRS = ROOT / "out/srs"

SRC_URLS = {
    "direct":  "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/direct.txt",
    "private": "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/private.txt",
    "cncidr":  "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/cncidr.txt",
    "google":  "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/google.txt",
}

def ensure_dirs():
    for p in [RULES, OUT_CLASH, OUT_SBOX, OUT_SRS]:
        p.mkdir(parents=True, exist_ok=True)

def fetch(url, path):
    with urllib.request.urlopen(url) as r:
        data = r.read()
    path.write_bytes(data)

def load_payload(yaml_path):
    # 支持 flow 序列/普通列表，返回 list[str]
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, list):
        raise ValueError(f"Invalid rule-set YAML: {yaml_path}")
    # 规整成纯字符串（去空白）
    return [str(x).strip() for x in payload if isinstance(x, (str, int)) and str(x).strip()]

def save_clash_yaml(payload_list, out_path):
    data = {"payload": payload_list}
    out_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

def split_domains(domains):
    """把域名列表拆成 suffix 与 exact 两类。
       规则：以 '+.' 开头 → suffix = 去掉 '+.'；否则 → exact"""
    suffixes, exacts = [], []
    for d in domains:
        d = d.strip().strip("'").strip('"')
        if d.startswith("+."):
            suffixes.append(d[2:])
        else:
            exacts.append(d)
    return suffixes, exacts

def normalize_domain(d):
    d = d.strip().strip("'").strip('"')
    if d.startswith("+."):
        d = d[2:]
    return d

def remove_intersections(direct_domains, google_suffixes):
    """删除 direct 中与任一 google 后缀相交的域（含其子域）"""
    gs = set(google_suffixes)
    result = []
    for d in direct_domains:
        raw = d.strip().strip("'").strip('"')
        dd = normalize_domain(raw)
        # 如果 direct 里是 '+.sub.example.com'，也处理成 dd = 'sub.example.com'
        hit = any(dd == s or dd.endswith("." + s) for s in gs)
        if not hit:
            result.append(raw if raw else d)
    return result

def to_singbox_source_for_domains(domains):
    suffixes, exacts = split_domains(domains)
    rules = []
    if suffixes:
        rules.append({"domain_suffix": sorted(set(suffixes))})
    if exacts:
        rules.append({"domain": sorted(set(exacts))})
    return {"version": 4, "rules": rules}

def to_singbox_source_for_ipcidr(cidrs):
    ips = [str(x).strip().strip("'").strip('"') for x in cidrs]
    return {"version": 4, "rules": [{"ip_cidr": ips}]}

def maybe_compile_srs(json_path, srs_path):
    try:
        # sing-box 1.12+： `sing-box rule-set compile --output out.srs in.json`
        subprocess.run(
            ["sing-box", "rule-set", "compile", "--output", str(srs_path), str(json_path)],
            check=True
        )
    except FileNotFoundError:
        print("sing-box 未安装，跳过 .srs 编译（CI 中会安装）")
    except subprocess.CalledProcessError as e:
        print("sing-box 编译失败：", e, file=sys.stderr)

def main():
    ensure_dirs()

    # 1) 下载源文件
    for name, url in SRC_URLS.items():
        dst = RULES / f"{name}.txt"
        print(f"Fetch {name} -> {dst}")
        fetch(url, dst)

    # 2) 读取 payload
    direct = load_payload(RULES / "direct.txt")
    private = load_payload(RULES / "private.txt")
    cncidr = load_payload(RULES / "cncidr.txt")
    google = load_payload(RULES / "google.txt")

    # 3) 用 google 后缀去重 direct
    g_suffixes, _ = split_domains(google)  # google 全是 '+.'
    direct_filtered = remove_intersections(direct, g_suffixes)

    # 4) 导出 Clash/Mihomo YAML
    save_clash_yaml(direct_filtered, OUT_CLASH / "direct.yaml")
    save_clash_yaml(private,        OUT_CLASH / "private.yaml")
    save_clash_yaml(cncidr,         OUT_CLASH / "cncidr.yaml")

    # 5) 生成 sing-box source JSON
    (OUT_SBOX / "direct.json").write_text(
        json.dumps(to_singbox_source_for_domains(direct_filtered), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT_SBOX / "private.json").write_text(
        json.dumps(to_singbox_source_for_domains(private), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT_SBOX / "cncidr.json").write_text(
        json.dumps(to_singbox_source_for_ipcidr(cncidr), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 6) 尝试本地编译 .srs（CI 中会真正跑）
    for name in ["direct", "private", "cncidr"]:
        maybe_compile_srs(OUT_SBOX / f"{name}.json", OUT_SRS / f"{name}.srs")

if __name__ == "__main__":
    main()
