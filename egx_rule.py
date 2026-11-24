# -*- coding: utf-8 -*-
"""
اختبار قاعدة «القائد» على أي فترة — من ملف egx_study.csv (نسخة ٢) اللي بيطلّعه egx_study.py.

القاعدة:
  القائد = السهم اللي طلع أكتر من الكل في آخر جلستين (من الأسهم اللي متوسط تداولها مليون جنيه+).
  من ١١:٠٠ لحد ١٢:٠٠ (أول نقطة فحص تتحقق فيها الشروط):
    ١) القائد فوق إقفال امبارح
    ٢) ٦٠٪+ من الأسهم طالعة
    ٣) السهم طالع ٥٪ لحد أقل من ٩٫٥٪، وبعده عن أعلى سعر في الجلسة أقل من ١٪
  الخروج بكرة: لو فتح تحت الوقف أو فوق الهدف نخرج بالفتح، وإلا هدف +٥٪ أو وقف −٥٪
  (لو الاتنين اتلمسوا نفترض الوقف)، وإلا نبيع آخر الجلسة. العمولة ٠٫٥٪ رايح جاي.

التشغيل (بعد ما تشغلي egx_study.py على الداتا الجديدة):
    $env:PYTHONIOENCODING="utf-8"
    py egx_rule.py egx_study.csv | Out-File -Encoding utf8 egx_rule.txt
بيطلع: egx_rule_trades.csv  ← ابعتيه هو وegx_rule.txt
"""
import sys

import numpy as np
import pandas as pd

print("نسخة قاعدة القائد ١", flush=True)
MIN_LIQ = 1e6
COST = 0.5
RULE = dict(ret_lo=5, ret_hi=9.5, breadth=0.6, from_high=-1, ck_from="11:00", ck_to="12:00",
            leader_min=0.0, target=5, stop=5)


def leaders(X):
    """القائد لكل جلسة: أعلى طلوع في الجلستين اللي قبلها (متصلين)."""
    L = X[X["check"] == "14:00"].copy()
    L["dr"] = ((1 + L["ret_prev %"] / 100) * (1 + L["to_close %"] / 100) - 1) * 100
    dates = sorted(X["date"].unique())
    prev = {dates[i + 1]: dates[i] for i in range(len(dates) - 1)
            if (pd.Timestamp(dates[i + 1]) - pd.Timestamp(dates[i])).days <= 4}
    DR = L.pivot_table(index="date", columns="sym", values="dr")
    out = {}
    for d in dates:
        p1 = prev.get(d)
        p2 = prev.get(p1) if p1 else None
        if not p1 or not p2 or p1 not in DR.index or p2 not in DR.index:
            continue
        r2 = (((1 + DR.loc[p1] / 100) * (1 + DR.loc[p2] / 100) - 1) * 100).dropna().sort_values(ascending=False)
        if len(r2):
            out[d] = (r2.index[0], round(float(r2.iloc[0]), 1))
    return out


def payoff(d, T, S):
    o, hi, lo, c = [d[k].to_numpy() for k in ["next_open %", "next_high %", "next_low %", "close_d1 %"]]
    r = np.where(o <= -S, o, np.where(o >= T, o, np.where(lo <= -S, -S, np.where(hi >= T, T, c))))
    return r - COST


def run(X, lead, rule, use_leader=True):
    r = rule
    m = ((X["ret_prev %"] >= r["ret_lo"]) & (X["ret_prev %"] < r["ret_hi"]) & (X["mkt_breadth"] >= r["breadth"]) &
         (X["from_high %"] >= r["from_high"]) & (X["check"] >= r["ck_from"]) & (X["check"] <= r["ck_to"]))
    if use_leader:
        m &= X["L_ret"] >= r["leader_min"]
    d = X[m & (X["sym"] != X["leader"])].sort_values(["check", "value_so_far"], ascending=[True, False])
    d = d.drop_duplicates(["date", "sym"]).copy()
    d["الربح %"] = payoff(d, r["target"], r["stop"]) if len(d) else []
    return d


def line(name, d, ndays):
    if not len(d):
        return f"{name}: مفيش صفقات"
    dm = d.groupby("date")["الربح %"].mean()
    return (f"{name}: {len(d)} صفقة في {d['date'].nunique()} يوم (من {ndays}) | متوسط {d['الربح %'].mean():+.2f}% "
            f"وسيط {d['الربح %'].median():+.2f}% | كسبانة {(d['الربح %'] > 0).mean():.0%} | أيام كسبانة {(dm > 0).mean():.0%} "
            f"| أسوأ يوم {dm.min():+.2f}% | أكبر يوم فيه {d['date'].value_counts().iloc[0]} صفقة")


def main():
    X = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else "egx_study.csv", encoding="utf-8-sig")
    X = X[X["avg_value_5d"] >= MIN_LIQ].dropna(subset=["close_d1 %", "next_high %", "next_low %", "next_open %"]).copy()
    lead = leaders(X)
    X["leader"] = X["date"].map(lambda d: lead.get(d, (None, None))[0])
    Ls = X[X["sym"] == X["leader"]][["date", "check", "ret_prev %"]].rename(columns={"ret_prev %": "L_ret"})
    X = X.merge(Ls, on=["date", "check"], how="left")
    X = X[X["leader"].notna()]
    ndays = X["date"].nunique()
    print(f"الجلسات اللي فيها قائد: {ndays} ({X['date'].min()} → {X['date'].max()})\n")

    d = run(X, lead, RULE)
    base = run(X, lead, RULE, use_leader=False)
    print("===== النتيجة (بعد عمولة ٠٫٥٪) =====")
    print(line("القاعدة بشرط القائد", d, ndays))
    print(line("من غير شرط القائد", base, ndays))
    blocked = base[~base.set_index(["date", "sym"]).index.isin(d.set_index(["date", "sym"]).index)]
    print(line("اللي شرط القائد شالها", blocked, ndays))

    if len(d):
        big = d["date"].value_counts().index[0]
        print(line(f"من غير أكبر يوم ({big})", d[d["date"] != big], ndays))
        mid = sorted(X["date"].unique())[ndays // 2]
        print(line("النص الأول", d[d["date"] < mid], ndays))
        print(line("النص التاني", d[d["date"] >= mid], ndays))
        d["الشهر"] = d["date"].str[:7]
        print("\nشهر بشهر:")
        for mth, g in d.groupby("الشهر"):
            print("  " + line(mth, g, ndays))

    print("\n===== ثبات القاعدة: تغيير الأرقام حواليها =====")
    for k, vals in [("ret_lo", [3, 4, 5, 6]), ("breadth", [0.5, 0.6, 0.7]), ("from_high", [-0.5, -1, -2]),
                    ("leader_min", [-1, 0, 2]), ("target", [3, 5, 7]), ("stop", [3, 5, 7])]:
        for v in vals:
            rr = dict(RULE, **{k: v})
            g = run(X, lead, rr)
            print(f"  {k}={v}: " + (line("", g, ndays)[2:] if len(g) else "مفيش"))

    print("\n===== يوم بيوم =====")
    days = X.groupby("date").agg(القائد=("leader", "first"))
    days["طلوع القائد في جلستين %"] = [lead[x][1] for x in days.index]
    lr = X[X["check"] == "11:00"].groupby("date")["L_ret"].first()
    br = X[X["check"] == "11:00"].groupby("date")["mkt_breadth"].first()
    days["القائد الساعة ١١ %"] = lr.round(1)
    days["الأسهم الطالعة الساعة ١١"] = (br * 100).round(0)
    if len(d):
        g = d.groupby("date")["الربح %"].agg(["count", "mean"]).rename(columns={"count": "صفقات", "mean": "متوسط %"})
        days = days.join(g.round(2))
    print(days.fillna({"صفقات": 0}).to_string())

    cols = ["date", "sym", "check", "price", "ret_prev %", "from_high %", "mkt_breadth", "leader", "L_ret",
            "next_open %", "next_high %", "next_low %", "close_d1 %", "الربح %"]
    d[cols].to_csv("egx_rule_trades.csv", index=False, encoding="utf-8-sig", float_format="%.3f")
    print("\nخلص ✅ — ابعتي egx_rule.txt وegx_rule_trades.csv")


if __name__ == "__main__":
    main()
