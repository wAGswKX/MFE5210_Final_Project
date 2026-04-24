import sqlite3
import matplotlib.pyplot as plt
from pathlib import Path

# ---- 中文字体配置 ----
plt.rcParams['font.sans-serif'] = ['SimHei', 'PingFang SC', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False
# -----------------------

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "processed" / "trading_system_b.sqlite3"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT SUM(fee), SUM(slippage) FROM trades")
fee_total, slippage_total = cursor.fetchone()
conn.close()

if fee_total is None or slippage_total is None:
    print("trades表中没有成交记录，请先运行 run_demo.py 生成一些成交。")
    exit()

labels = ['手续费 (Fee)', '滑点 (Slippage)']
sizes = [fee_total, slippage_total]
colors = ['#ff9999', '#66b3ff']
explode = (0.05, 0.05)

plt.figure(figsize=(6, 6))
plt.pie(sizes, explode=explode, labels=labels, colors=colors, autopct='%1.1f%%',
        shadow=False, startangle=140, textprops={'fontsize': 12})
plt.title(f'交易成本构成\n总手续费: {fee_total:.2f}  总滑点: {slippage_total:.2f}', fontsize=14)
plt.axis('equal')
plt.tight_layout()
plt.savefig('tca_cost_pie.png', dpi=150, bbox_inches='tight')
plt.show()
print("饼图已保存为 tca_cost_pie.png")