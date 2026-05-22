import streamlit as st
import requests
from bs4 import BeautifulSoup
import networkx as nx
import pandas as pd
import plotly.express as px
from urllib.parse import urljoin, urlparse
import time
from pyvis.network import Network
import streamlit.components.v1 as components
from opencc import OpenCC
from concurrent.futures import ThreadPoolExecutor

# 初始化繁體中文轉換器 (改善2)
cc = OpenCC('s2twp') # 簡體中文轉台灣正體

# --- 1. 高效並行爬蟲核心邏輯 (解決卡頓、增加3) ---
def fetch_single_url(args):
    """單一網頁爬取核心，供多執行緒呼叫"""
    url, headers, max_links = args
    links = set()
    try:
        response = requests.get(url, headers=headers, timeout=3) # 縮短 timeout 避免卡死
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                full_url = urljoin(url, a['href'])
                if urlparse(full_url).scheme in ['http', 'https']:
                    links.add(full_url)
                if len(links) >= max_links:
                    break
    except:
        pass
    return url, links

def crawl_web_parallel(start_url, max_per_layer=20, max_layers=2):
    """使用執行緒池進行多層網頁爬取 (增加1)"""
    G = nx.DiGraph()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    current_layer_urls = {start_url}
    visited_urls = set()
    
    # 增加3：評估總爬取量並提示
    estimated_max = sum([max_per_layer**i for i in range(1, max_layers + 1)])
    if estimated_max > 500:
        st.warning(f"⚠️ 警告：當前設定最大可能爬取 {estimated_max} 個節點，耗時較長，請耐心等候。")

    status_text = st.empty()
    progress_bar = st.progress(0)

    for layer in range(max_layers):
        status_text.write(f"🕸️ 正在分析第 {layer + 1} 層網頁 (當前層節點數: {len(current_layer_urls)})...")
        
        # 過濾已爬過的網址
        urls_to_crawl = [u for u in current_layer_urls if u not in visited_urls]
        if not urls_to_crawl:
            break
            
        # 建立並行任務
        tasks = [(url, headers, max_per_layer) for url in urls_to_crawl]
        next_layer_urls = set()
        
        # 啟動 15 個執行緒並行下載
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = executor.map(fetch_single_url, tasks)
            
            for i, (parent_url, child_links) in enumerate(results):
                visited_urls.add(parent_url)
                for link in child_links:
                    G.add_edge(parent_url, link)
                    next_layer_urls.add(link)
                progress_bar.progress(min((i + 1) / len(tasks), 1.0))
                
        current_layer_urls = next_layer_urls
        time.sleep(0.1)
        
    status_text.write("✅ 網路圖譜建構完成！")
    return G

# --- 2. 評分與視覺化組件 ---
def draw_influence_bar(current_val, df):
    max_val = df['權重值'].max()
    score = (current_val / max_val) * 100 if max_val > 0 else 0
    score = round(score, 1)

    if score <= 20: label, color = "極弱", "#9ca3af"
    elif score <= 40: label, color = "弱", "#fbbf24"
    elif score <= 60: label, color = "一般", "#60a5fa"
    elif score <= 80: label, color = "強", "#8b5cf6"
    else: label, color = "極強", "#ef4444"

    bar_html = f"""
    <div style="font-family: sans-serif; margin: 20px 0;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
            <span style="font-weight: bold; color: {color};">影響力評級：{label}</span>
            <span style="font-weight: bold;">{score} / 100</span>
        </div>
        <div style="width: 100%; background-color: #e5e7eb; border-radius: 10px; height: 25px; position: relative;">
            <div style="width: {score}%; background-color: {color}; height: 100%; border-radius: 10px;"></div>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 10px; color: #6b7280; margin-top: 5px;">
            <span>0% (極弱)</span><span>20%</span><span>40% (弱)</span><span>60% (一般)</span><span>80% (強)</span><span>100% (極強)</span>
        </div>
    </div>
    """
    components.html(bar_html, height=90)

def draw_interactive_graph(G, df):
    # 建立 Pyvis 網路圖
    net = Network(height="600px", width="100%", bgcolor="#f8fafc", font_color="#1e293b", directed=True)
    
    top_nodes = df['網址'].head(40).tolist()
    max_weight = df['權重值'].max()
    
    for _, row in df.head(40).iterrows():
        url = row['網址']
        node_size = 15 + (row['權重值'] / max_weight * 50)
        
        # 改善2：連結名稱轉為繁體字並過濾
        raw_label = urlparse(url).netloc if len(url) > 20 else url
        clean_label = cc.convert(raw_label) 
        
        # 改善3：用顏色區別權威網站 (前20%為紅色權威節點，其餘為藍色)
        current_score = (row['權重值'] / max_weight) * 100
        node_color = "#ef4444" if current_score >= 60 else "#60a5fa"
        
        net.add_node(url, label=clean_label, title=url, size=node_size, color=node_color)

    for u, v in G.edges():
        if u in top_nodes and v in top_nodes:
            net.add_edge(u, v, color="#cbd5e1", arrows="to")
    
    net.toggle_physics(True)
    net.set_options('{"physics": {"forceAtlas2Based": {"gravitationalConstant": -60, "centralGravity": 0.01, "springLength": 120}, "solver": "forceAtlas2Based"}}')
    
    try:
        net.save_graph("graph.html")
        with open("graph.html", 'r', encoding='utf-8') as f:
            components.html(f.read(), height=650)
    except:
        st.error("拓樸圖生成失敗。")

# --- 3. Streamlit 主介面 ---
st.set_page_config(page_title="PageRank 權重分析儀表板", layout="wide")

# 建立功能分頁 (增加2：新增隨機/假帳號辨識區)
tab1, tab2 = st.tabs(["🕸️ 網頁 PageRank 分析", "📸 IG 異常帳號辨識模擬區"])

# --- TAB 1: 網頁分析系統 ---
with tab1:
    st.title("🕸️ 網頁影響力 PageRank 分析系統")
    
    if 'G' not in st.session_state: st.session_state.G = None
    if 'df' not in st.session_state: st.session_state.df = None

    with st.sidebar:
        st.header("⚙️ 分析設定")
        start_url = st.text_input("起始網址", value="https://www.wikipedia.org")
        max_links = st.slider("每層爬取上限", 5, 100, 25)
        # 增加1：搜尋層數設定
        max_layers = st.slider("搜尋層數 (深度)", 1, 3, 2, help="建議設定2層，3層資料量極大")
        alpha = st.slider("阻尼係數 (Alpha)", 0.0, 1.0, 0.85)
        analyze_btn = st.button("開始執行深度分析")

    if analyze_btn:
        with st.spinner("正在進行高效並行爬取..."):
            G_res = crawl_web_parallel(start_url, max_links, max_layers)
            pagerank_scores = nx.pagerank(G_res, alpha=alpha)
            
            # 改善2：將所有儲存的網址轉換為繁體字
            df_res = pd.DataFrame([
                {"網址": cc.convert(k), "權重值": v} for k, v in pagerank_scores.items()
            ])
            df_res = df_res.sort_values(by='權重值', ascending=False).reset_index(drop=True)
            
            st.session_state.G = G_res
            st.session_state.df = df_res

    if st.session_state.df is not None:
        df = st.session_state.df
        G = st.session_state.G

        st.divider()
        col1, col2 = st.columns([4, 6])

        with col1:
            st.subheader("🏆 權重值排名 (Top 10)")
            st.dataframe(df.head(10), use_container_width=True)
            
            st.subheader("📊 全域權重分佈佔比")
            fig_pie = px.pie(df.head(15), values='權重值', names='網址', hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)

        with col2:
            st.subheader("🎯 特定節點深入分析")
            selected_site = st.selectbox("請選擇欲分析的網頁：", df['網址'].tolist())
            
            current_weight = df.loc[df['網址'] == selected_site, '權重值'].values[0]
            draw_influence_bar(current_weight, df)

            # 改善1：可自由選取圖表類型的下游權重分佈
            st.write("### 📍 下遊連結權重分佈")
            chart_type = st.radio("選擇統計圖表類型：", ["直方圖", "折線圖", "圓餅圖"], horizontal=True)
            
            # 尋找下游節點 (考慮繁體字匹配)
            successors = list(G.successors(selected_site)) if selected_site in G else []
            if not successors and selected_site.endswith('/'):
                successors = list(G.successors(selected_site[:-1])) if selected_site[:-1] in G else []

            if successors:
                sub_df = df[df['網址'].isin([cc.convert(s) for s in successors])]
                
                if chart_type == "直方圖":
                    fig_sub = px.bar(sub_df, x='網址', y='權重值', title="下游網頁權重直方圖", color='權重值')
                elif chart_type == "折線圖":
                    fig_sub = px.line(sub_df, x='網址', y='權重值', title="下游網頁權重趨勢圖", markers=True)
                else:
                    fig_sub = px.pie(sub_df, values='權重值', names='網址', title="下游網頁權重佔比圓餅圖")
                    
                st.plotly_chart(fig_sub, use_container_width=True)
            else:
                st.warning("此網頁在本次分析層級中無下游連結，或屬於邊緣葉節點。")

        st.divider()
        st.subheader("🌳 網頁關係拓樸圖 (互動式)")
        st.info("💡 🔴 紅色節點代表影響力評分 > 60% 的高權威網站；🔵 藍色節點為一般網站。")
        draw_interactive_graph(G, df)
    else:
        st.info("請於左側選單設定網址，並按下分析按鈕。")

# --- TAB 2: IG 假帳號辨識模擬區 (增加2) ---
with tab2:
    st.title("📸 Instagram 異常/假帳號辨識模擬系統")
    st.markdown("""
    ### 💡 核心邏輯：結構網路與 PageRank 的逆向應用
    真實社會中，**權威網站**會有很多「高質量的網站」連向它（PageRank 高）。
    而在社交軟體中，**假帳號/水軍** 具備特殊的圖論特徵：
    1. **出度 (Out-degree) 極高**：瘋狂追蹤別人，但極少人回追。
    2. **互聯水軍網**：大量假帳號之間會互相追蹤以充人頭，形成一個封閉的「強連通分量」。
    """)
    
    st.subheader("🤖 模擬測試：輸入社群互動數據進行判定")
    
    # 模擬資料輸入
    col_ig1, col_ig2 = st.columns(2)
    with col_ig1:
        username = st.text_input("輸入欲檢測的帳號 ID", value="@bot_test_999")
        followers = st.number_input("粉絲數 (Followers)", value=12)
        following = st.number_input("追蹤中 (Following)", value=1450)
    with col_ig2:
        post_count = st.number_input("發文數量", value=2)
        avg_likes = st.number_input("近10篇貼文平均按讚數", value=0)

    # 權重判定計算
    ff_ratio = following / followers if followers > 0 else following
    
    st.write("### 🔍 異常特徵分析報告")
    
    # 建立量化指標
    fake_score = 0
    if ff_ratio > 20: fake_score += 40  # 追蹤比例嚴重不對稱
    if post_count < 3: fake_score += 30  # 幾無內容
    if avg_likes == 0: fake_score += 30  # 無真實互動
    
    # 顯示結果進度條
    st.progress(fake_score / 100)
    
    if fake_score >= 70:
        st.error(f"🚨 判定結果：帳號 {username} 具備 【極高機率為假帳號/機器人】 的特徵 (風險值: {fake_score}%)")
        st.markdown("""
        - **圖論結構分析**：該節點展現出極高的發散邊 (Out-edges)，且其入權重 (PageRank Score) 趨近於零，符合典型水軍導流節點特徵。
        """)
    elif fake_score >= 40:
        st.warning(f"⚠️ 判定結果：帳號 {username} 狀態異常 (風險值: {fake_score}%)")
    else:
        st.success(f"✅ 判定結果：帳號 {username} 表現正常 (風險值: {fake_score}%)")
