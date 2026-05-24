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

# 初始化繁體中文轉換器
cc = OpenCC('s2twp') # 簡體中文轉台灣正體

# --- 1. 高效並行爬蟲核心邏輯 ---
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
    """使用執行緒池進行多層網頁爬取"""
    G = nx.DiGraph()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    current_layer_urls = {start_url}
    visited_urls = set()
    
    # 評估總爬取量並提示
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
        
        raw_label = urlparse(url).netloc if len(url) > 20 else url
        clean_label = cc.convert(raw_label) 
        
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

# Helper 函數：精簡長網址為更具辨識度的名稱
def get_short_label(url):
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]
    if path_parts:
        # 特別優化維基百科常見的詞條路徑
        return cc.convert(path_parts[-1])
    return cc.convert(parsed.netloc)

# --- 3. Streamlit 主介面 ---
st.set_page_config(page_title="PageRank 權重分析儀表板", layout="wide")

# 功能分頁
tab1, tab2 = st.tabs(["🕸️ 網頁 PageRank 分析", "📸 IG 異常帳號辨識模擬區"])

# --- TAB 1: 網頁分析系統 ---
with tab1:
    st.title("🕸️ 網頁影響力 PageRank 分析系統")
    
    if 'G' not in st.session_state: st.session_state.G = None
    if 'df' not in st.session_state: st.session_state.df = None

    with st.sidebar:
        st.header("⚙️ 分析設定")
        start_url = st.text_input("起始網址", value="https://zh.wikipedia.org")
        max_links = st.slider("每層爬取上限", 5, 100, 25)
        max_layers = st.slider("搜尋層數 (深度)", 1, 3, 2, help="建議設定2層，3層資料量極大")
        alpha = st.slider("阻尼係數 (Alpha)", 0.0, 1.0, 0.85)
        analyze_btn = st.button("開始執行深度分析")

    if analyze_btn:
        with st.spinner("正在進行高效並行爬取..."):
            G_res = crawl_web_parallel(start_url, max_links, max_layers)
            pagerank_scores = nx.pagerank(G_res, alpha=alpha)
            
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
            st.subheader("🏆 全域權重值排名 (Top 10)")
            # 建立一個精簡名稱欄位供主表格呈現
            display_df = df.head(10).copy()
            display_df.insert(0, '網頁名稱', display_df['網址'].apply(get_short_label))
            st.dataframe(display_df[['網頁名稱', '權重值']], use_container_width=True)
            
            st.subheader("📊 全域權重分佈佔比")
            pie_df = df.head(15).copy()
            pie_df['網頁名稱'] = pie_df['網址'].apply(get_short_label)
            fig_pie = px.pie(pie_df, values='權重值', names='網頁名稱', hole=0.4,
                             hover_data={'網址': True})
            st.plotly_chart(fig_pie, use_container_width=True)

        with col2:
            st.subheader("🎯 特定節點深入分析")
            selected_site = st.selectbox("請選擇欲分析的網頁：", df['網址'].tolist())
            
            current_weight = df.loc[df['網址'] == selected_site, '權重值'].values[0]
            draw_influence_bar(current_weight, df)

            # 下游權重分佈區塊
            st.write("### 📍 下遊連結權重分佈")
            chart_type = st.radio("選擇統計圖表類型：", ["直方圖", "折線圖", "圓餅圖"], horizontal=True)
            
            successors = list(G.successors(selected_site)) if selected_site in G else []
            if not successors and selected_site.endswith('/'):
                successors = list(G.successors(selected_site[:-1])) if selected_site[:-1] in G else []

            if successors:
                # 篩選下游資料
                sub_df = df[df['網址'].isin([cc.convert(s) for s in successors])].copy()
                
                # 【優化 1：將長網址縮短為名稱，完整網址塞進 hover_data】
                sub_df['顯示名稱'] = sub_df['網址'].apply(get_short_label)
                sub_df = sub_df.sort_values(by='權重值', ascending=False)

                # 【優化 2：動態生成圖表，橫軸文字不再傾斜】
                if chart_type == "直方圖":
                    fig_sub = px.bar(
                        sub_df, 
                        x='顯示名稱', 
                        y='權重值', 
                        title=f"【{get_short_label(selected_site)}】的下游網頁權重直方圖", 
                        color='權重值',
                        color_continuous_scale='Blues',
                        hover_data={'網址': True, '顯示名稱': False, '權重值': ':.6f'}
                    )
                    fig_sub.update_layout(xaxis_tickangle=0, xaxis_title="網頁名稱 (滑鼠懸停看完整網址)")

                elif chart_type == "折線圖":
                    fig_sub = px.line(
                        sub_df, 
                        x='顯示名稱', 
                        y='權重值', 
                        title=f"【{get_short_label(selected_site)}】的下游網頁權重趨勢圖", 
                        markers=True,
                        hover_data={'網址': True, '顯示名稱': False, '權重值': ':.6f'}
                    )
                    fig_sub.update_layout(xaxis_tickangle=0, xaxis_title="網頁名稱 (滑鼠懸停看完整網址)")

                else:
                    fig_sub = px.pie(
                        sub_df, 
                        values='權重值', 
                        names='顯示名稱', 
                        title=f"【{get_short_label(selected_site)}】的下游網頁權重佔比圓餅圖",
                        hole=0.3,
                        hover_data={'網址': True}
                    )
                    fig_sub.update_traces(textposition='inside', textinfo='percent+label')
                    
                st.plotly_chart(fig_sub, use_container_width=True)

                # 【優化 3：進階圖表數據統計與白話文結構洞察解說】
                st.markdown("#### 📊 統計數據深度解說")
                
                total_links = len(sub_df)
                max_node = sub_df.iloc[0]
                min_node = sub_df.iloc[-1]
                avg_weight = sub_df['權重值'].mean()
                std_weight = sub_df['權重值'].std()
                
                # 計算最大節點吞噬了下游總權重的多少比例
                top_1_share = (max_node['權重值'] / sub_df['權重值'].sum()) * 100 if sub_df['權重值'].sum() > 0 else 0

                # 建立精美的統計 Metric 看板
                c1, c2, c3 = st.columns(3)
                c1.metric("下游總節點數 (出度)", f"{total_links} 個")
                c2.metric("平均分配權重值", f"{avg_weight:.5f}")
                c3.metric("最大核心節點佔比", f"{top_1_share:.1f}%")

                # 提供白話文的圖論結構脈絡解釋
                st.markdown("> **💡 網路圖論結構洞察報告：**")
                
                if top_1_share > 50:
                    structure_desc = f"⚠️ **權力高度集中型結構**：下游網頁中，極高比例的權重被單一網站吞噬。這代表當前選取的網頁具有強烈的**導流單一性**，資訊或流量幾乎全權交由單一核心節點吸收。"
                elif std_weight < 0.005 if not pd.isna(std_weight) else True:
                    structure_desc = "🤝 **權力均平型結構**：下游各網頁之間的權重標準差極低，分配得極為均勻。這意味著當前網頁是一個**中立型門戶網站**（如維基百科首頁），它對所有分支連結一視同仁，沒有刻意向特定站點偏袒導流。"
                else:
                    structure_desc = f"📈 **階層式分散結構**：流量與權重呈階梯式向外遞減傳遞，網路生態分層健康。流量的第一受益者為 `{max_node['顯示名稱']}`，最末端分流則為 `{min_node['顯示名稱']}`。"

                st.markdown(f"""
                * 🔝 **最強下游分支**：`{max_node['網址']}` （分得權重：`{max_node['權重值']:.6f}`）
                * 🔚 **最弱下游分支**：`{min_node['網址']}` （分得權重：`{min_node['權重值']:.6f}`）
                * 📊 **拓樸特徵判定**：{structure_desc}
                """)
            else:
                st.warning("此網頁在本次分析層級中無下游連結，或屬於邊緣葉節點。")

        st.divider()
        st.subheader("🌳 網頁關係拓樸圖 (互動式)")
        st.info("💡 🔴 紅色節點代表影響力評分 > 60% 的高權威網站；🔵 藍色節點為一般網站。")
        draw_interactive_graph(G, df)
else:
    st.info("請於左側選單設定網址，並按下分析按鈕。")

# --- TAB 2: IG 假帳號辨識模擬區 ---
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
    
    col_ig1, col_ig2 = st.columns(2)
    with col_ig1:
        username = st.text_input("輸入欲檢測的帳號 ID", value="@bot_test_999")
        followers = st.number_input("粉絲數 (Followers)", value=12)
        following = st.number_input("追蹤中 (Following)", value=1450)
    with col_ig2:
        post_count = st.number_input("發文數量", value=2)
        avg_likes = st.number_input("近10篇貼文平均按讚數", value=0)

    ff_ratio = following / followers if followers > 0 else following
    
    st.write("### 🔍 異常特徵分析報告")
    
    fake_score = 0
    if ff_ratio > 20: fake_score += 40  
    if post_count < 3: fake_score += 30  
    if avg_likes == 0: fake_score += 30  
    
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
