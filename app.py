"""ReqTrace review UI - every AI/rule output can be accepted, rejected or
overridden with a comment, and every decision lands in the audit log.

    streamlit run app.py
"""
from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from reqtrace import maintain, supplier
from reqtrace.cli import load_cases
from reqtrace.export import deviations_markdown, matrix_rows, spec_markdown
from reqtrace.llm import LLM
from reqtrace.model import Compliance, Decision
from reqtrace.select import extract_features, select_requirements
from reqtrace.store import ROOT, open_store

st.set_page_config(page_title="ReqTrace", layout="wide")


@st.cache_resource
def get_store():
    return open_store()


store = get_store()
llm = LLM()
cases = load_cases()

st.sidebar.title("ReqTrace")
reviewer = st.sidebar.text_input("Reviewer", "engineer")
mode = st.sidebar.radio("Analysis mode", ["hybrid", "rules", "ai"],
                        help="hybrid = rules first, AI where rules are not confident")
st.sidebar.caption("AI layer: " + (f"Groq `{llm.model}`" if llm.available
                                    else "off (GROQ_API_KEY not set) - rules only"))
page = st.sidebar.radio("Page", ["Library", "Maintenance", "Case requirements",
                                 "Supplier response", "Audit log"])


def log(action: str, obj: str, **detail):
    store.log(Decision(actor=reviewer, action=action, obj=obj, detail=detail))


# --- Library -----------------------------------------------------------------
if page == "Library":
    st.header("Requirements library")
    show_retired = st.checkbox("Show retired")
    reqs = store.current(include_retired=show_retired)
    df = pd.DataFrame([{"id": r.id, "v": r.version, "status": r.status.value,
                        "title": r.title, "applies_if": ", ".join(r.applies_if) or "always",
                        "source": r.source_ref, "verification": r.verification,
                        "last_reviewed": r.last_reviewed} for r in reqs])
    st.dataframe(df, hide_index=True, width="stretch")
    rid = st.selectbox("Version history", [r.id for r in reqs])
    for v in store.history(rid):
        st.markdown(f"**v{v.version}** ({v.status.value}, {v.source_ref}) - {v.text}")

# --- Maintenance -------------------------------------------------------------
elif page == "Maintenance":
    st.header("Library maintenance")
    st.caption("Proposals change nothing until approved. Approval creates a new version.")
    findings = maintain.check_library(store.current())
    kinds = sorted({f.kind for f in findings})
    pick = st.multiselect("Kinds", kinds, default=kinds)
    for i, f in enumerate([f for f in findings if f.kind in pick]):
        with st.container(border=True):
            st.markdown(f"**{f.kind}** - {', '.join(f.req_ids)}: {f.message}")
            for rid in f.req_ids:
                r = store.get(rid)
                st.caption(f"{rid} v{r.version}: {r.text}")
            proposal = dict(f.proposal)
            target = proposal.pop("_target", f.req_ids[0])
            if f.kind == "vague" and llm.available:
                if st.button("Draft verifiable rewrite with AI", key=f"ai{i}"):
                    d = maintain.propose_rewrite(store.get(target), llm)
                    if d:
                        st.session_state[f"draft{i}"] = d.proposal
                proposal = st.session_state.get(f"draft{i}", proposal)
            if proposal:
                st.json({"target": target, **proposal})
            comment = st.text_input("Comment / reason", key=f"c{i}")
            c1, c2 = st.columns(2)
            if proposal and c1.button("Approve proposal", key=f"ok{i}"):
                store.new_version(target, proposal, reviewer, comment or f.message)
                st.success(f"{target} updated")
                st.rerun()
            if c2.button("Reject / not an issue", key=f"no{i}"):
                log("reject_finding", f"req:{target}", kind=f.kind, message=f.message,
                    comment=comment)
                st.info("Rejection logged")

# --- Case requirements -------------------------------------------------------
elif page == "Case requirements":
    st.header("Requirements for a procurement case")
    cid = st.selectbox("Case", list(cases), format_func=lambda c: f"{c} - {cases[c].title}")
    case = cases[cid]
    st.write(case.description)
    with st.spinner("Identifying machine features"):
        hits, notes = extract_features(case.description, mode, llm)
    st.subheader("Features")
    st.dataframe(pd.DataFrame([{"feature": h.feature, "found by": h.source,
                                "evidence": h.evidence, "reason": h.reason} for h in hits]),
                 hide_index=True, width="stretch")
    for n in notes:
        st.warning(n)
    from reqtrace.features import FEATURES
    extra = st.multiselect("Add features the system missed", [f for f in FEATURES
                                                             if f not in {h.feature for h in hits}])
    feats = {h.feature for h in hits} | set(extra)
    sel = select_requirements(feats, store.current())
    st.subheader(f"Selected requirements ({len(sel)})")
    ai_feats = {h.feature for h in hits if h.source == "ai"}
    keep = {}
    for s in sel:
        via_ai = bool(s.because) and set(s.because) <= ai_feats
        label = (f"{s.req.id} {s.req.title} - because: {', '.join(s.because) or 'always'}"
                 + ("  [AI-suggested]" if via_ai else ""))
        keep[s.req.id] = st.checkbox(label, value=True, key=f"k{cid}{s.req.id}")
    if st.button("Confirm selection"):
        log("confirm_selection", f"case:{cid}", included=[k for k, v in keep.items() if v],
            excluded=[k for k, v in keep.items() if not v], added_features=extra)
        st.success("Selection logged")
    chosen = [s for s in sel if keep[s.req.id]]
    st.download_button("Download specification (.md)", spec_markdown(case, hits, chosen),
                       file_name=f"{cid}_specification.md")

# --- Supplier response -------------------------------------------------------
elif page == "Supplier response":
    st.header("Supplier response analysis")
    files = sorted((ROOT / "data" / "responses").glob("*.csv"))
    up = st.file_uploader("Upload a response (CSV: req_id, claimed, answer)", type="csv")
    path = None
    if up:
        path = ROOT / "data" / "responses" / "_uploaded.csv"
        path.write_bytes(up.getvalue())
    else:
        path = st.selectbox("Or pick an example", files, format_func=lambda p: p.stem)
    answers = supplier.load_answers(path)
    lib = {r.id: r for r in store.current(include_retired=True)}
    with st.spinner("Assessing answers"):
        results = [supplier.assess(lib[a.req_id], a, mode, llm) if a.req_id in lib else None
                   for a in answers]
    pairs = [(a, r) for a, r in zip(answers, results) if r is not None]
    n_review = sum(r.needs_review for _, r in pairs)
    st.metric("Answers needing human review", f"{n_review} / {len(pairs)}")
    only = st.checkbox("Show only those needing review", value=True)
    decisions = st.session_state.setdefault(f"dec_{path.stem}", {})
    for a, r in pairs:
        if only and not r.needs_review:
            continue
        with st.container(border=True):
            req = lib[a.req_id]
            st.markdown(f"**{a.req_id} {req.title}** - {req.text}")
            st.markdown(f"> {a.answer or '_(no answer)_'}  \n_ticked: {a.claimed or '-'}_")
            st.markdown(f"Assessment: **{r.status.value}** ({r.layer}, confidence {r.confidence:.2f})"
                        + (f" - evidence: \"{r.evidence}\"" if r.evidence else "")
                        + (f" - {r.reason}" if r.reason else ""))
            for fl in r.flags:
                st.warning(fl)
            c1, c2 = st.columns([1, 2])
            new = c1.selectbox("Decision", [s.value for s in Compliance],
                               index=list(Compliance).index(r.status), key=f"s{path.stem}{a.req_id}")
            com = c2.text_input("Comment", key=f"m{path.stem}{a.req_id}")
            if st.button("Record decision", key=f"b{path.stem}{a.req_id}"):
                action = "accept" if new == r.status.value else "override"
                decisions[a.req_id] = {"status": new, "action": action, "comment": com}
                log(action, f"resp:{path.stem}:{a.req_id}", ai_status=r.status.value,
                    final=new, layer=r.layer, comment=com)
                st.success(f"{action} logged")
    rows = matrix_rows([a for a, _ in pairs], [r for _, r in pairs], lib, decisions)
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    c1, c2 = st.columns(2)
    c1.download_button("Compliance matrix (.csv)", buf.getvalue().encode("utf-8-sig"),
                       file_name=f"{path.stem}_compliance_matrix.csv")
    c2.download_button("Deviation list (.md)", deviations_markdown(rows),
                       file_name=f"{path.stem}_deviations.md")

# --- Audit log ---------------------------------------------------------------
else:
    st.header("Audit log")
    log_rows = store.audit()
    st.dataframe(pd.DataFrame([{"time": d.ts, "who": d.actor, "action": d.action,
                                "object": d.obj, "detail": d.detail} for d in reversed(log_rows)]),
                 hide_index=True, width="stretch")
