"""Streamlit demo: the e-commerce refund conflict scenario from CLAUDE.md.
payment-agent (mock Stripe API) and support-agent (mock Zendesk ticket)
disagree about a refund; mnemos resolves it with visible reasoning;
fulfillment-agent reads memory and correctly avoids a duplicate refund;
a time-travel view shows what was believed before vs. after resolution.

Run with: streamlit run src/demo/app.py
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.api.client import MnemosClient
from src.schema.db import get_connection
from src.verification.ledger import upsert_refund_status

STATUS_ICONS = {"canonical": "✅", "superseded": "❌", "candidate": "🕓", "contested": "⚠️"}

st.set_page_config(page_title="mnemos demo", page_icon="🧠", layout="wide")


def ensure_demo_sources():
    if "stripe_source_id" in st.session_state:
        return st.session_state.stripe_source_id, st.session_state.zendesk_source_id
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            stripe_id = uuid.uuid4()
            cur.execute(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
                (stripe_id, f"streamlit-stripe-{uuid.uuid4().hex[:6]}", 5, "Payment processor API - system of record"),
            )
            zendesk_id = uuid.uuid4()
            cur.execute(
                "INSERT INTO sources (id, name, authority_tier, description) VALUES (%s, %s, %s, %s)",
                (zendesk_id, f"streamlit-zendesk-{uuid.uuid4().hex[:6]}", 3, "Support ticket text, human-entered"),
            )
        conn.commit()
    finally:
        conn.close()
    st.session_state.stripe_source_id = stripe_id
    st.session_state.zendesk_source_id = zendesk_id
    return stripe_id, zendesk_id


def ensure_order():
    if "order_id" not in st.session_state:
        st.session_state.order_id = uuid.uuid4().hex[:8]
        st.session_state.subject_key = None
        st.session_state.log = []
        st.session_state.timestamps = {}


def new_order():
    st.session_state.order_id = uuid.uuid4().hex[:8]
    st.session_state.subject_key = None
    st.session_state.log = []
    st.session_state.timestamps = {}


st.title("🧠 mnemos — agentic memory with conflict resolution")
st.caption(
    "E-commerce refund scenario: payment-agent (Stripe) and support-agent (Zendesk) "
    "disagree about a refund. Watch mnemos resolve it, and time-travel through the result."
)

ensure_order()
stripe_id, zendesk_id = ensure_demo_sources()
client = MnemosClient()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Order")
    st.code(f"order-{st.session_state.order_id}")
    st.button("🔄 Start a new order", on_click=new_order)

    st.subheader("1. Trigger the conflict")
    trigger_disabled = st.session_state.subject_key is not None
    if st.button("▶ Send conflicting claims", disabled=trigger_disabled):
        # Real ground-truth record, independent of either agent's claim -
        # what mnemos checks directly instead of only weighing the two
        # claims against each other by authority tier. See
        # src/resolution/verification.py.
        conn = get_connection()
        try:
            upsert_refund_status(conn, st.session_state.order_id, "processed", 49.99)
        finally:
            conn.close()

        with st.spinner("support-agent reading Zendesk ticket..."):
            support_result = client.add(
                f"Zendesk ticket: customer says refund for order-{st.session_state.order_id} is still pending",
                agent_id="support-agent", source_id=zendesk_id,
            )
        st.session_state.log.append(("support-agent", support_result))
        st.session_state.timestamps["after_support"] = datetime.now(timezone.utc)

        with st.spinner("payment-agent reading the Stripe API..."):
            payment_result = client.add(
                f"Stripe webhook: refund.processed for order-{st.session_state.order_id}",
                agent_id="payment-agent", source_id=stripe_id,
            )
        st.session_state.log.append(("payment-agent", payment_result))
        st.session_state.timestamps["after_payment"] = datetime.now(timezone.utc)
        st.session_state.subject_key = payment_result.subject_key
        st.rerun()

    if trigger_disabled:
        st.caption("Conflict already triggered for this order - start a new order to run it again.")

    if st.session_state.log:
        st.subheader("2. What happened")
        for agent_name, result in st.session_state.log:
            with st.expander(f"{agent_name} → {result.outcome}", expanded=True):
                st.write(f"**Claim:** {result.claim_text}")
                if result.detail:
                    st.info(f"**Resolution reasoning:** {result.detail}")

with col2:
    if st.session_state.subject_key:
        subject_key = st.session_state.subject_key

        st.subheader("3. fulfillment-agent checks memory")
        results = client.search("refund status", subject_key=subject_key)
        if results:
            canonical = results[0]
            st.success(f'Canonical answer: "{canonical.claim_text}"  (source: {canonical.source_name})')
            st.caption("→ fulfillment-agent correctly does NOT issue a duplicate refund.")
        else:
            st.warning("No canonical answer yet - contested, awaiting human review.")

        st.subheader("4. Live memory state")
        for b in client.get_all(subject_key, include_superseded=True):
            icon = STATUS_ICONS.get(b.status, "•")
            st.write(f"{icon} **[{b.status}]** {b.claim_text}  \n_source: {b.source_name}, confidence: {b.confidence}_")

        st.subheader("5. Time-travel")
        st.caption("What did mnemos believe at each point in time?")
        option_labels = {
            "after_support": "Right after support-agent's claim (before resolution)",
            "after_payment": "Right after payment-agent's claim (conflict resolved)",
            "now": "Right now",
        }
        options = list(st.session_state.timestamps.keys()) + ["now"]
        choice = st.selectbox(
            "View memory state:", options,
            format_func=lambda k: option_labels.get(k, k), index=len(options) - 1,
        )
        as_of_ts = datetime.now(timezone.utc) if choice == "now" else st.session_state.timestamps[choice]
        as_of_result = client.as_of(subject_key, as_of_ts)
        st.code(as_of_result.pretty(), language=None)
    else:
        st.info("← Trigger the conflict to see memory state, resolution, and time-travel.")
