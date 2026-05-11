import streamlit as st
import requests
from bs4 import BeautifulSoup
import networkx as nx
import pandas as pd
import plotly.express as px
from urllib.parse import urljoin, urlparse, unquote
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
        status_text.text(f"正在爬取 ({i+1}/{total}): {unquote(link)[:50]}...")
        l2_links = get_links(link, headers, max_per_layer)
        for l2 in l2_links:
            G.add_edge(link, l2)
        progress_bar.progress((i + 1) / total)
        time.sleep(0.05)
        
    status_text.empty()
    progress_bar.empty()
    return G

# --- 2. 視覺化工具 ---
def generate_network_html(G, pagerank_dict):
    net = Network(height="600px", width="100%", bgcolor="#222222", font_color="white", directed=True)
    if not pagerank_dict: return ""
    max_pr = max(pagerank_dict.values())
    for node in G.nodes():
        score = pagerank_dict.get(node, 0)
        size = (score / max_pr) * 50 + 10 
        display_name = unquote(node)
        net.add_node(node, label=display_name.split('/')[-1] or display_name, title=display_name, size=size, color="#4face6")
    for source, target in G.edges():
        net.add_edge(source, target, color="#666666")
    net.force_atlas_2based()
    return net.generate_html()

# --- 3. Streamlit 介面設定 ---
st.set_page_config(page_title="網頁 PageRank 分析系統", layout="wide")
st.title("🌐 網頁權重影響力分析儀表板")

with st.sidebar:
    st.header("⚙️ 參數設定")
    start_url = st.text_input("起始網址", value="https://zh.wikipedia.org/wiki/三上悠亞")
    max_links = st.slider("每層抓取上限", 5, 50, 15)
    alpha = st.slider("PageRank 阻尼係數", 0.0, 1.0, 0.85)
    analyze_btn = st.button("開始分析", type="primary")
    st.info("提示：分析包含兩層連結，結果將顯示原生語言名稱。")

if 'data' not in st.session_state:
    st.session_state.data = None

if analyze_btn:
    with st.spinner("正在進行深度網路爬取與數據計算..."):
        G = crawl_web(start_url, max_links)
        scores = nx.pagerank(G, alpha=alpha)
        df = pd.DataFrame(list(scores.items()), columns=['網址', '權重值'])
        # 直接解碼，保留原始語言（泰文、日文、簡繁中文等）
        df['網頁名稱'] = df['網址'].apply(unquote)
        df = df.sort_values(by='權重值', ascending=False).reset_index(drop=True)
        st.session_state.data = {"G": G, "df": df, "scores": scores}

# --- 4. 結果呈現 (重新調整版面結構) ---
if st.session_state.data:
    data = st.session_state.data
    df = data["df"]
    G = data["G"]

    # 指標欄
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("總節點數", G.number_of_nodes())
    m2.metric("總連線數", G.number_of_edges())
    m3.metric("平均權重值", f"{df['權重值'].mean():.5f}")
    m4.metric("分析深度", "2 Layers")

    st.divider()

    # 第一區塊：權重排名表格 (全寬度顯示)
    st.subheader("📊 權重排名與連結")
    st.dataframe(
        df[['網頁名稱', '權重值', '網址']], 
        column_config={
            "網網名稱": st.column_config.TextColumn("網頁名稱"),
            "網址": st.column_config.LinkColumn("前往連結", display_text="🔗 點擊開啟"),
            "權重值": st.column_config.NumberColumn("PageRank 權重", format="%.6f")
        },
        use_container_width=True,
        hide_index=True
    )

    st.divider()

    # 第二區塊：特定節點深入分析 (移至下方，佔據全寬度避免圖表擠壓)
    st.subheader("🎯 特定節點深入分析 (下游權重分佈)")
    
    # 建立名稱對應字典
    name_to_url = dict(zip(df['網頁名稱'], df['網址']))
    selected_name = st.selectbox("選擇要查看的網頁節點", df['網頁名稱'].tolist())
    selected_url = name_to_url[selected_name]

    # 提供直接開啟連結
    st.link_button(f"🌐 直接開啟選中網頁", selected_url)

    successors = list(G.successors(selected_url))
    if successors:
        # 篩選下游節點的數據
        sub_df = df[df['網址'].isin(successors)]
        
        # 繪製大型圓餅圖
        fig_pie = px.pie(
            sub_df, 
            values='權重值', 
            names='網頁名稱', 
            hole=0.4,
            height=600  # 增加高度
        )
        # 優化圖例顯示，避免擠壓
        fig_pie.update_layout(
            legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.05),
            margin=dict(l=20, r=20, t=50, b=20)
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.warning(f"「{selected_name}」在本次分析範圍內沒有發現下遊連結。")

    st.divider()

    # 第三區塊：互動式網路圖
    st.subheader("🕸️ 互動式網路拓樸圖")
    st.caption("滾輪可縮放，點擊並拖動節點可觀察連結關係。")
    html_content = generate_network_html(G, data["scores"])
    components.html(html_content, height=650)

else:
    st.write("### 👆 請在左側輸入網址並按下「開始分析」")
    st.markdown("""
    本系統將執行以下操作：
    1. 抓取您輸入的起始網頁。
    2. 找出該網頁內的所有外部/內部連結 (Layer 1)。
    3. 針對這些連結再深入抓取一層 (Layer 2)。
    4. 透過 **PageRank 演算法** 計算所有節點的影響力權重。
    """)
