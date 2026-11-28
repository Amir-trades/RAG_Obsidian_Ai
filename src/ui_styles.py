from __future__ import annotations

import streamlit as st


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        #MainMenu, footer {visibility: hidden;}
        .block-container {
            max-width: 860px;
            padding-top: 1.8rem;
            padding-bottom: 7rem;
        }
        [data-testid="stChatMessage"] {
            border-radius: 18px;
            padding: 1rem 1.15rem;
            margin-bottom: .8rem;
        }
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
            background: #232836;
        }
        [data-testid="stSidebar"] {
            background: #171A21;
        }
        [data-testid="stSidebar"] [data-testid="stButton"] button {
            border-radius: 12px;
            text-align: left;
        }
        .aqua-badge {
            display: inline-block;
            padding: .35rem .7rem;
            border-radius: 999px;
            background: #273653;
            color: #dce7ff;
            font-size: .85rem;
            margin: .25rem 0 .75rem;
        }
        .aqua-saved {
            background: #173a2d;
            color: #c4f3d9;
        }
        .aqua-welcome {
            text-align: center;
            padding: 2rem 1rem 1.2rem;
        }
        div[data-testid="stButton"] button[kind="secondary"] {
            min-height: 3.2rem;
            border-radius: 14px;
            border-color: #303746;
            background: #171A21;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
