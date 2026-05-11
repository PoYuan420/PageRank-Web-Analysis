import streamlit as st
import requests
from bs4 import BeautifulSoup
import networkx as nx
import pandas as pd
import plotly.express as px
from urllib.parse import urljoin, urlparse, unquote  # 引入 unquote 來解碼中文
import time
from pyvis.network import Network
import streamlit.components.v1 as components

# --- 1. 爬蟲核心邏輯 ---
def get_links(url, headers, max_links):
    links = set()
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                full_url = urljoin(url, a['href'])
                clean_url = full_url.split('#')[0].rstrip('/')
                ext = urlparse(clean_url).path.lower()
                if urlparse(clean_url).scheme in ['http', 'https'] and \
                   not any(ext.endswith(x) for x in ['.pdf', '.jpg', '.png', '.zip', '.docx']):
                    links.add(clean_url)
                if len(links) >= max_links:
                    break
    except:
        pass 
    return links

def crawl_web(start_url, max_per_layer=20):
    G = nx.DiGraph()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    layer1_links = get_links(start_url, headers, max_per_layer)
    for link in layer1_links:
        G.add_edge(start_url, link)
        
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(layer1_links)
    
    for i, link in enumerate(layer1_links):
        status_text.text(f"正在分析第 {i+1}/{total} 個網站...")
        l2_links = get_links(link, headers, max_per_layer)
        for l2 in l2_links:
            G.add_edge(link, l2)
        progress_bar.progress((i + 1) / total)
        time.sleep(0.05)
        
    status_text.empty()
    return G

# --- 2. 視覺化工具 ---
def generate_network_html(G, pagerank_dict):
    net = Network(height="500px", width="100%", bgcolor="#222222", font_color="white", directed=True)
    max_pr = max(pagerank_dict.values()) if pagerank_dict else 1
    for node in G.nodes():
        score = pagerank_dict.get(node, 0)
        size = (score / max_pr) * 50 + 10 
        # 節點標籤使用解碼後的中文
        display_name = unquote(node)
        net.add_node(node, label=display_name.split('/')[-1] or display_name, title=display_name, size=size, color="#4face6")
    for source, target in G.edges():
        net.add_edge(source, target, color="#666666")
    net.force_atlas_2based()
    return net.generate_html()

# --- 3. Streamlit 介面 ---
st.set_page_config(page_title="網頁權重分析系統", layout="wide")
st.title("🌐 網頁權重與關聯分析儀表板")

with st.sidebar:
    st.header("🔍 分析設定")
    start_url = st.text_input("輸入起始網址", value="https://zh.wikipedia.org/wiki/Wikipedia:%E9%A6%96%E9%A1%B5")
    max_links = st.slider("每層最大抓取數", 5, 50, 15)
    alpha = st.slider("PageRank 阻尼係數", 0.0, 1.0, 0.85)
    analyze_btn = st.button("開始分析", type="primary")

if 'data' not in st.session_state:
    st.session_state.data = None

if analyze_btn:
    with st.spinner("分析中..."):
        G = crawl_web(start_url, max_links)
        scores = nx.pagerank(G, alpha=alpha)
        # 建立 DataFrame 並加入「解碼網址」供顯示
        df = pd.DataFrame(list(scores.items()), columns=['網址', '權重值'])
        df['網頁名稱'] = df['網址'].apply(unquote) # 解碼中文
        df = df.sort_values(by='權重值', ascending=False).reset_index(drop=True)
        st.session_state.data = {"G": G, "df": df, "scores": scores}

# --- 4. 結果呈現 ---
if st.session_state.data:
    data = st.session_state.data
    df = data["df"]
    G = data["G"]

    # 頂部指標
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("總節點", G.number_of_nodes())
    c2.metric("總連線", G.number_of_edges())
    c3.metric("最高權重", f"{df['權重值'].max():.4f}")
    c4.metric("目標域名", urlparse(start_url).netloc)

    st.divider()

    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("📊 權重排名與連結")
        # 使用 LinkColumn 讓網址可以點擊
        st.dataframe(
            df[['網頁名稱', '權重值', '網址']], 
            column_config={
                "網址": st.column_config.LinkColumn("前往連結", display_text="點擊開啟"),
                "權重值": st.column_config.NumberColumn("權重", format="%.5f")
            },
            use_container_width=True,
            hide_index=True
        )

    with col_right:
        st.subheader("🎯 特定節點深入分析")
        # 讓下拉選單顯示解碼後的中文名稱
        name_to_url = dict(zip(df['網頁名稱'], df['網址']))
        selected_name = st.selectbox("選擇要分析的網頁", df['網頁名稱'].tolist())
        selected_url = name_to_url[selected_name]

        # 點擊按鈕直接跳轉
        st.link_button(f"🚀 直接開啟：{selected_name[:30]}...", selected_url)

        successors = list(G.successors(selected_url))
        if successors:
            sub_df = df[df['網址'].isin(successors)]
            fig_pie = px.pie(sub_df, values='權重值', names='網頁名稱', hole=0.4, title="下游權重分佈")
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.warning("此網頁在本次分析中無下游連結。")

    st.divider()
    st.subheader("🕸️ 互動式網路拓樸圖")
    html_content = generate_network_html(G, data["scores"])
    components.html(html_content, height=600)
else:
    st.info("請點擊左側「開始分析」按鈕。")
