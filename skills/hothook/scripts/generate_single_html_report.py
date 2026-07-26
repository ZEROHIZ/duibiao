#!/usr/bin/env python3
"""
HotHook 单文件 HTML 报告生成器 (generate_single_html_report.py)

核心职责：
1. 解析 Markdown 并将其转换为 `小A_蒸馏报告.html` 风格的高级模块化 (module/module-inv) HTML 报告。
2. 支持特殊的行内样式与定制化标签映射 (If-Then 逻辑块、数据翻牌器 Dashboard)。
3. 内置专属 learnings 与 CSS 变量。
"""

from __future__ import annotations

import argparse
import base64
import html
import mimetypes
import pathlib
import re
from datetime import datetime

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a single-file HotHook HTML report")
    parser.add_argument("--markdown", required=True, help="Markdown report written by the agent")
    parser.add_argument("--out", required=True, help="Output HTML file")
    parser.add_argument("--title", default="HotHook 完整拆解报告")
    parser.add_argument("--embed", action="append", default=[], help="Image file or directory to embed; can be repeated")
    return parser.parse_args()


def parse_inline(text: str) -> str:
    safe = html.escape(text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<em>\1</em>", safe)
    safe = re.sub(r"`(.+?)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\[(.+?)\]\((.+?)\)", r"<a href='\2' target='_blank'>\1</a>", safe)
    return safe


def render_if_then_block(text: str) -> str:
    # Remove the [IF-THEN] marker
    text = text.replace("[IF-THEN]", "").strip()
    
    # Try to extract IF: and THEN:
    if_match = re.search(r"IF:\s*(.*?)(?=THEN:|$)", text, flags=re.IGNORECASE | re.DOTALL)
    then_match = re.search(r"THEN:\s*(.*)", text, flags=re.IGNORECASE | re.DOTALL)
    
    if_text = if_match.group(1).strip() if if_match else "Condition"
    then_text = then_match.group(1).strip() if then_match else text
    
    return f"""
    <div class="if-then-block">
        <div class="if-cond">IF / {parse_inline(if_text)}</div>
        <div class="then-action">{parse_inline(then_text)}</div>
    </div>
    """


def render_dashboard_grid(rows: list[list[str]]) -> str:
    html_parts = ['<div class="dashboard-grid">']
    for row in rows:
        if len(row) >= 2:
            label = row[0]
            val = row[1]
            note = row[2] if len(row) > 2 else ""
            
            # Try to extract a clean number for data-target
            num_match = re.search(r"[\d\.]+", val.replace(",", ""))
            data_target = num_match.group(0) if num_match else "0"
            decimals = "1" if "." in data_target else "0"
            
            html_parts.append(f"""
            <div class="dash-cell">
                <div class="stat-label">{parse_inline(label)}</div>
                <div class="stat-val" data-target="{data_target}" data-decimals="{decimals}">0</div>
                {f'<div class="stat-note">{parse_inline(note)}</div>' if note else ''}
            </div>
            """)
    html_parts.append('</div>')
    return "\n".join(html_parts)


def markdown_to_html(markdown: str) -> str:
    modules_html = []
    current_module = {"id": "", "title": "", "content": [], "is_inv": False, "raw_title": ""}
    
    # Pre-defined inverse modules
    inv_titles = ["一眼看清", "数据面板", "核心结论", "数据表现"]
    
    in_table = False
    table_rows = []
    
    def flush_table():
        nonlocal in_table, table_rows
        if not in_table:
            return ""
            
        rows = [row for row in table_rows if not re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*", row)]
        parsed_rows = []
        for row in rows:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            parsed_rows.append(cells)
            
        table_html = ""
        # If inside a dashboard module, render as dashboard-grid
        if "数据面板" in current_module["raw_title"] or "一眼看清" in current_module["raw_title"]:
            # Skip header row if it exists
            data_rows = parsed_rows[1:] if len(parsed_rows) > 1 and parsed_rows[0][0].lower() in ["指标", "metric"] else parsed_rows
            table_html = render_dashboard_grid(data_rows)
        else:
            html_rows = []
            for index, cells in enumerate(parsed_rows):
                tag = "th" if index == 0 else "td"
                html_rows.append("<tr>" + "".join(f"<{tag}>{parse_inline(c)}</{tag}>" for c in cells) + "</tr>")
            table_html = "<table>" + "".join(html_rows) + "</table>"
            
        in_table = False
        table_rows = []
        return table_html

    def flush_module():
        if current_module["content"]:
            flush_table_res = flush_table()
            if flush_table_res:
                current_module["content"].append(flush_table_res)
                
            inv_class = " module-inv" if current_module["is_inv"] else ""
            visible_class = " visible" if current_module["id"] == "01" else ""
            
            # Left column
            mod_left = f'<div class="mod-left"><div class="mod-num">{current_module["id"]}</div></div>'
            
            # Right column
            mod_right_content = []
            mod_right_content.append(f'<div class="sys-label">{current_module["id"]} / {parse_inline(current_module["raw_title"])}</div>')
            mod_right_content.append(f'<h2>{parse_inline(current_module["title"])}</h2>')
            mod_right_content.append('<div class="divider-wrap"><div class="divider-line"></div></div>')
            mod_right_content.extend(current_module["content"])
            
            mod_right = f'<div class="mod-right">{"".join(mod_right_content)}</div>'
            
            mod_id_attr = f'id="mod{int(current_module["id"])}"' if current_module["id"].isdigit() else ""
            modules_html.append(f'<div {mod_id_attr} class="module{inv_class}{visible_class}">{mod_left}{mod_right}</div>')
        
        current_module["content"] = []

    lines = markdown.splitlines()
    in_blockquote = False
    blockquote_lines = []
    
    def flush_blockquote():
        nonlocal in_blockquote, blockquote_lines
        if not in_blockquote:
            return
            
        bq_text = "\n".join(blockquote_lines)
        if "[IF-THEN]" in bq_text:
            current_module["content"].append(render_if_then_block(bq_text))
        else:
            current_module["content"].append(f"<blockquote>{parse_inline(bq_text)}</blockquote>")
            
        in_blockquote = False
        blockquote_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        
        # Filter comments
        if line.startswith("#") and not line.startswith("##") and not line.startswith("# "):
            stripped = line.lstrip("#").strip()
            if (not stripped or "/" in stripped or "\\" in stripped or 
                "核心职责" in stripped or "仅作为" in stripped or 
                stripped.endswith(".md") or "report.md" in stripped.lower()):
                continue

        # Table handling
        if "|" in line and line.strip().startswith("|"):
            flush_blockquote()
            in_table = True
            table_rows.append(line)
            continue
        
        table_html = flush_table()
        if table_html:
            current_module["content"].append(table_html)

        # Blockquote handling
        if line.startswith(">"):
            in_blockquote = True
            blockquote_lines.append(line.lstrip(">").strip())
            continue
            
        flush_blockquote()

        if not line.strip():
            continue

        # Module matching: ## 01 / 数据面板 or ## 01 | 数据面板 or ## 1. 数据面板
        mod_match = re.match(r"##\s*0?(\d+)\s*[/|.-]?\s*(.*)", line)
        if mod_match:
            flush_module()
            mod_id = f"{int(mod_match.group(1)):02d}"
            raw_title = mod_match.group(2).strip()
            
            is_inv = any(inv_t in raw_title for inv_t in inv_titles)
            
            current_module["id"] = mod_id
            current_module["title"] = raw_title
            current_module["raw_title"] = raw_title
            current_module["is_inv"] = is_inv
            continue
            
        # Top level title (ignore, usually report title)
        if line.startswith("# "):
            continue
            
        # Sub-sections
        if line.startswith("### "):
            current_module["content"].append(f'<div class="sys-label" style="margin-top: 20px;">{parse_inline(line[4:].strip())}</div>')
        elif line.startswith("- "):
            current_module["content"].append(f'<p class="bullet">{parse_inline(line[2:].strip())}</p>')
        else:
            current_module["content"].append(f'<p class="main-text">{parse_inline(line)}</p>')
            
    flush_table()
    flush_blockquote()
    flush_module()
    
    return "\n".join(modules_html)


def main() -> int:
    args = parse_args()
    markdown_path = pathlib.Path(args.markdown)
    out_path = pathlib.Path(args.out)
    
    markdown_text = markdown_path.read_text(encoding="utf-8-sig")
    body_html = markdown_to_html(markdown_text)
    
    generation_date = datetime.now().strftime("%Y-%m-%d")

    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(args.title)}</title>
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Noto+Serif+SC:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    /* Reset & Base */
    * {{
      box-sizing: border-box;
      border-radius: 0 !important;
      box-shadow: none !important;
    }}
    body {{
      background-color: #CEC9C0;
      color: #1A1211;
      font-family: 'Noto Serif SC', serif;
      font-size: 16px;
      line-height: 1.7;
      margin: 0;
      padding: 0;
    }}
    
    /* Sticky Top Status Bar */
    .status-bar {{
      position: sticky;
      top: 0;
      background-color: #0A0806;
      color: #FAF6F2;
      font-family: 'Space Mono', monospace;
      font-size: 13px;
      letter-spacing: 0.05em;
      padding: 10px 32px;
      z-index: 1000;
      border-bottom: 1px solid #7A6E65;
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    .status-bar span {{ margin-right: 15px; }}
    .status-bar span:last-child {{ margin-right: 0; }}
    .blink {{ animation: status-blink 2s infinite; }}
    @keyframes status-blink {{
      0%, 100% {{ opacity: 1; }}
      50% {{ opacity: 0.6; }}
    }}

    /* Container */
    .container {{
      max-width: 1000px;
      margin: 0 auto;
      padding: 0 32px;
    }}

    /* Modules */
    .module {{
      display: grid;
      grid-template-columns: 100px 1fr;
      gap: 48px;
      padding: 56px 0;
      border-bottom: 1px solid #1A1211;
      opacity: 0;
      transform: translateY(20px);
      transition: opacity 0.6s ease, transform 0.6s ease;
    }}
    .module.visible {{
      opacity: 1;
      transform: translateY(0);
    }}
    
    /* Inverse Modules */
    .module-inv {{
      background-color: #8A3926;
      color: #FAF6F2;
      margin: 0 -32px;
      padding: 56px 32px;
      border-bottom: 1px solid #1A1211;
    }}
    /* Let top module #mod1 be immediately visible */
    #mod1.module {{
      opacity: 1;
      transform: translateY(0);
    }}

    /* Column Styles */
    .mod-left {{
      display: flex;
      justify-content: flex-start;
      align-items: flex-start;
    }}
    .mod-num {{
      font-family: 'Space Mono', monospace;
      font-size: 80px;
      font-weight: 700;
      line-height: 1;
      color: rgba(26, 18, 17, 0.09);
      user-select: none;
    }}
    .module-inv .mod-num {{
      color: rgba(250, 246, 242, 0.15);
    }}

    .mod-right {{
      display: flex;
      flex-direction: column;
    }}

    /* Typo Hierarchy */
    .sys-label {{
      font-family: 'Space Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.1em;
      color: #7A6E65;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}
    .module-inv .sys-label {{
      color: rgba(250, 246, 242, 0.6);
    }}

    h2 {{
      font-size: 22px;
      font-weight: 700;
      margin: 0 0 12px 0;
      line-height: 1.4;
    }}

    /* Dividers */
    .divider-wrap {{
      width: 100%;
      height: 1px;
      margin: 4px 0 24px 0;
      overflow: hidden;
    }}
    .divider-line {{
      width: 0;
      height: 1px;
      background: #1A1211;
    }}
    .module-inv .divider-line {{
      background: rgba(250, 246, 242, 0.4);
    }}
    .module.visible .divider-line {{
      width: 100%;
      transition: width 0.8s ease;
      transition-delay: 200ms;
    }}

    /* Typography assignments */
    .main-text {{
      font-size: 15px;
      margin-bottom: 16px;
    }}
    .bullet {{
      font-size: 15px;
      margin: 0 0 8px 0;
      padding-left: 20px;
      position: relative;
    }}
    .bullet::before {{
      content: "▪";
      position: absolute;
      left: 2px;
      color: #8A3926;
      font-size: 12px;
    }}
    .module-inv .bullet::before {{
      color: #FAF6F2;
    }}
    
    .secondary-text {{
      font-size: 14px;
      color: #7A6E65;
    }}
    .module-inv .secondary-text {{
      color: rgba(250, 246, 242, 0.7);
    }}

    /* details summary */
    details {{
      background: rgba(0, 0, 0, 0.05);
      border: 1px solid #7A6E65;
      margin-top: 15px;
      padding: 10px 15px;
    }}
    .module-inv details {{
      background: rgba(250, 246, 242, 0.05);
      border: 1px solid rgba(250, 246, 242, 0.3);
    }}
    summary {{
      font-family: 'Space Mono', monospace;
      font-size: 13px;
      cursor: pointer;
      font-weight: 700;
      outline: none;
      user-select: none;
      padding: 5px 0;
    }}
    details[open] summary {{
      border-bottom: 1px solid rgba(122, 110, 101, 0.3);
      margin-bottom: 10px;
      padding-bottom: 8px;
      padding-top: 5px;
    }}

    /* IF -> THEN blocks */
    .if-then-block {{
      border-left: 4px solid #8A3926;
      background: rgba(138, 57, 38, 0.04);
      padding: 15px 20px;
      margin-bottom: 20px;
    }}
    .module-inv .if-then-block {{
      border-left: 4px solid #FAF6F2;
      background: rgba(250, 246, 242, 0.08);
    }}
    .if-cond {{
      font-family: 'Space Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      color: #8A3926;
      margin-bottom: 4px;
      text-transform: uppercase;
    }}
    .module-inv .if-cond {{
      color: #FAF6F2;
    }}
    .then-action {{
      font-size: 15px;
      font-weight: 500;
      margin-bottom: 8px;
    }}

    /* Tables */
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 20px;
    }}
    th, td {{
      border: 1px solid #7A6E65;
      padding: 10px 12px;
      text-align: left;
    }}
    .module-inv th, .module-inv td {{
      border: 1px solid rgba(250, 246, 242, 0.3);
    }}
    th {{
      font-family: 'Space Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      background: rgba(0, 0, 0, 0.03);
    }}
    .module-inv th {{
      background: rgba(250, 246, 242, 0.05);
    }}
    td {{
      font-size: 14px;
    }}

    /* Data Dashboard Grid */
    .dashboard-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 20px;
    }}
    .dash-cell {{
      border: 1px solid rgba(250, 246, 242, 0.3);
      padding: 15px;
      text-align: center;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }}
    .stat-label {{
      font-family: 'Space Mono', monospace;
      font-size: 11px;
      color: rgba(250, 246, 242, 0.6);
      margin-bottom: 8px;
      text-transform: uppercase;
    }}
    .stat-val {{
      font-family: 'Space Mono', monospace;
      font-size: 20px;
      font-weight: 700;
      color: #1A1211;
    }}
    .module-inv .stat-val {{
      color: #FAF6F2;
    }}
    .stat-note {{
      font-family: 'Space Mono', monospace;
      font-size: 12px;
      color: rgba(26, 18, 17, 0.5);
      margin-top: 4px;
    }}
    .module-inv .stat-note {{
      color: rgba(250, 246, 242, 0.5);
    }}
    
    blockquote {{
      border-left: 4px solid #8A3926;
      background: rgba(138, 57, 38, 0.04);
      padding: 15px 20px;
      margin: 15px 0;
      font-size: 14px;
    }}
    .module-inv blockquote {{
      border-left: 4px solid #FAF6F2;
      background: rgba(250, 246, 242, 0.08);
    }}

    /* Responsive */
    @media (max-width: 768px) {{
      .module {{
        grid-template-columns: 1fr;
        gap: 20px;
        padding: 40px 0;
      }}
      .module-inv {{
        margin: 0 -16px;
        padding: 40px 16px;
      }}
      .mod-num {{
        font-size: 48px;
      }}
      .dashboard-grid {{
        grid-template-columns: repeat(2, 1fr);
      }}
    }}

    /* Prefers Reduced Motion */
    @media (prefers-reduced-motion: reduce) {{
      .module, .module-inv {{
        opacity: 1 !important;
        transform: none !important;
        transition: none !important;
      }}
      .divider-line {{
        width: 100% !important;
        transition: none !important;
      }}
      .blink {{
        animation: none !important;
      }}
    }}
  </style>
</head>
<body>
  <!-- Sticky Top Status Bar -->
  <div class="status-bar">
    <span>SUBJECT: HOTHOOK ANALYSIS</span>
    <span>GENERATED: {generation_date}</span>
    <span>STATUS: <span class="blink">DECONSTRUCTED</span></span>
  </div>

  <div class="container">
    {body_html}
  </div>

  <script>
    document.addEventListener('DOMContentLoaded', () => {{
      const modules = document.querySelectorAll('.module:not(.module-inv)');
      const moduleObserver = new IntersectionObserver((entries, observer) => {{
        entries.forEach(entry => {{
          if (entry.isIntersecting) {{
            entry.target.classList.add('visible');
            if (entry.target.querySelector('.dashboard-grid')) {{
                animateCounters(entry.target);
            }}
            observer.unobserve(entry.target);
          }}
        }});
      }}, {{ threshold: 0.15 }});

      modules.forEach(mod => moduleObserver.observe(mod));

      const invModules = document.querySelectorAll('.module-inv:not(#mod1)');
      const invObserver = new IntersectionObserver((entries, observer) => {{
        entries.forEach(entry => {{
          if (entry.isIntersecting) {{
            entry.target.classList.add('visible');
            if (entry.target.querySelector('.dashboard-grid')) {{
                animateCounters(entry.target);
            }}
            observer.unobserve(entry.target);
          }}
        }});
      }}, {{ threshold: 0.15 }});

      invModules.forEach(mod => invObserver.observe(mod));
      
      // mod1 is immediately visible
      const mod1 = document.getElementById('mod1');
      if (mod1 && mod1.querySelector('.dashboard-grid')) {{
          setTimeout(() => animateCounters(mod1), 300);
      }}

      function animateCounters(container) {{
        const counters = container.querySelectorAll('[data-target]');
        counters.forEach(counter => {{
          const target = parseFloat(counter.getAttribute('data-target'));
          const decimals = parseInt(counter.getAttribute('data-decimals') || '0');
          const duration = 1800;
          let startTime = null;

          function update(timestamp) {{
            if (!startTime) startTime = timestamp;
            const progress = Math.min((timestamp - startTime) / duration, 1);
            const easeProgress = 1 - Math.pow(1 - progress, 3);
            const currentValue = easeProgress * target;
            counter.textContent = currentValue.toFixed(decimals);
            if (progress < 1) {{
              requestAnimationFrame(update);
            }} else {{
              counter.textContent = target.toFixed(decimals);
            }}
          }}
          requestAnimationFrame(update);
        }});
      }}
    }});
  </script>
</body>
</html>
"""
    out_path.write_text(document, encoding="utf-8")
    print(out_path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
