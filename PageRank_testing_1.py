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
from opencc import OpenCC
import concurrent.futures

# 初始化繁簡轉換器 (S2TW: 簡體到台灣正體)
cc = OpenCC('s2tw')

# --- 1. 爬蟲核心邏輯 (引入多執行緒優化效能) ---
def get_links(url, headers, max_links):
    links = set()
    try:
        # 設定較短的 timeout 避免因死連結卡頓
        response = requests.get(url, headers=headers, timeout=3)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                full_url = urljoin(url, a['href'])
                clean_url = full_url.split('#')[0].rstrip('/')
                ext = urlparse(clean_url).path.lower()
                if urlparse(clean_url).scheme in ['http', 'https'] and \
                   not any(ext.endswith(x) for x in ['.pdf', '.jpg', '.png', '.zip', '.docx']):
                    # 自動將抓到的網址與名稱進行繁體化處理
                    links.add(clean_url)
                if len(links) >= max_links:
                    break
    except:
        pass 
    return links

def crawl_web_parallel(start_url, max_per_layer=20, max_layers=2):
    G = nx.DiGraph()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    current_layer_nodes = {start_url}
    all_visited = {start_url}
    
    # 建立進度條介面
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    for layer in range(max_layers):
        progress_text.text(f"🕷️ 正在爬取第 {layer + 1} 層網路...")
        next_layer_nodes = set()
        
        # 使用 ThreadPoolExecutor 進行多執行緒並發爬取，大幅減少卡頓
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_url = {executor.submit(get_links, url, headers, max_per_layer): url for url in current_layer_nodes}
            
            for i, future in enumerate(concurrent.futures.as_completed(future_to_url)):
                source_url = future_to_url[future]
                try:
                    links = future.result()
                    for link in links:
                        G.add_edge(source_url, link)
                        if link not in all_visited:
                            next_layer_nodes.add(link)
                            all_visited.add(link)
                except:
                    pass
                # 更新單層內的微幅進度
                progress_bar.progress((i + 1) / len(current_layer_nodes))
                
        current_layer_nodes = next_layer_nodes
        if not current_layer_nodes:
            break
            
    progress_text.empty()
    progress_bar.empty()
    return G

# --- 2. 視覺化工具 (改善：使用顏色區別權威網站) ---
def generate_network_html(G, pagerank_dict):
    # 使用暗色系，讓彩色節點更明顯
    net = Network(height="600px", width="100%", bgcolor="#222222", font_color="white", directed=True)
    if not pagerank_dict: return ""
    
    max_pr = max(pagerank_dict.values()) if pagerank_dict else 1
    avg_pr = sum(pagerank_dict.values()) / len(pagerank_dict) if pagerank_dict else 0
    
    for node in G.nodes():
        score = pagerank_dict.get(node, 0)
        # 節點大小隨 PR 值縮放
        size = (score / max_pr) * 50 + 10 
        
        # 轉成繁體中文名稱
        display_name = cc.convert(unquote(node))
        label_name = display_name.split('/')[-1] or display_name
        
        # 【改善 3】根據權重值用顏色區別權威度
        if score > avg_pr * 3:
            color = "#ef4444"  # 核心權威：紅色
        elif score > avg_pr * 1.5:
            color = "#a855f7"  # 高影響力：紫色
        else:
            color = "#3b82f6"  # 一般節點：藍色
            
        net.add_node(node, label=label_name, title=f"網址: {display_name}<br>PageRank: {score:.6f}", size=size, color=color)
        
    for source, target in G.edges():
        net.add_edge(source, target, color="#555555")
        
    net.force_atlas_2based()
    return net.generate_html()

# --- 3. Streamlit 介面設定 ---
st.set_page_config(page_title="網頁 PageRank & 社交防詐分析系統", layout="wide")
st.title("🌐 網頁權重影響力與社交防詐分析儀表板")

# 建立兩個分頁，將 PR 系統與新功能隔離
tab1, tab2 = st.tabs(["🕸️ 網頁 PageRank 分析", "🛡️ Instagram 假帳號 PR 識別區"])

# --- TAB 1: PAGERANK 分析系統 ---
with tab1:
    with st.sidebar:
        st.header("⚙️ 參數設定")
        start_url = st.text_input("起始網址", value="https://zh.wikipedia.org/zh-tw/%E5%B7%A8%E7%9F%B3%E5%BC%B7%E6%A3%AE")
        max_links = st.slider("每層抓取上限", 5, 100, 15)
        
        # 【增加 1】新增搜尋層數設置，通常 2~3 層即為極限
        max_layers = st.slider("搜尋網絡層數深度", 1, 3, 2)
        alpha = st.slider("PageRank 阻尼係數", 0.0, 1.0, 0.85)
        
        # 【增加 3】太多網站的提示警告
        total_estimated = max_links ** max_layers
        if total_estimated >= 1000:
            st.warning(f"⚠️ 警告：當前設定的最大預估節點數達 {total_estimated} 個，可能會拉長計算時間或造成瀏覽器渲染拓樸圖時卡頓。")
            
        analyze_btn = st.button("開始分析", type="primary", key="pr_btn")

    if 'data' not in st.session_state:
        st.session_state.data = None

    if analyze_btn:
        with st.spinner("正在進行網絡爬取與矩陣運算..."):
            G = crawl_web_parallel(start_url, max_links, max_layers)
            
            if G.number_of_nodes() == 0:
                st.error("無法從該網址抓取任何連結，請檢查網址有效性。")
            else:
                scores = nx.pagerank(G, alpha=alpha)
                df = pd.DataFrame(list(scores.items()), columns=['網址', '權重值'])
                
                # 【改善 2】連結名稱進行解碼並全面轉換為繁體中文
                df['網頁名稱'] = df['網址'].apply(lambda x: cc.convert(unquote(x)))
                df = df.sort_values(by='權重值', ascending=False).reset_index(drop=True)
                st.session_state.data = {"G": G, "df": df, "scores": scores}

    if st.session_state.data:
        data = st.session_state.data
        df = data["df"]
        G = data["G"]

        # 指標看板
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("網頁總節點數", G.number_of_nodes())
        m2.metric("發現總連線數", G.number_of_edges())
        m3.metric("平均 PageRank 值", f"{df['權重值'].mean():.5f}")
        m4.metric("設定探測深度", f"{max_layers} Layers")

        st.divider()

        # 排名表格展示
        st.subheader("📊 繁體化權重排名與安全連結")
        st.dataframe(
            df[['網頁名稱', '權重值', '網址']], 
            column_config={
                "網頁名稱": st.column_config.TextColumn("網頁名稱 (已轉正體)"),
                "網址": st.column_config.LinkColumn("前往連結", display_text="🔗 開啟網站"),
                "權重值": st.column_config.NumberColumn("影響力權重", format="%.6f")
            },
            use_container_width=True, hide_index=True
        )

        st.divider()

        # 【改善 1】多功能圖表切換區
        st.subheader("🎯 特定節點深入分析 (下游權重分佈)")
        name_to_url = dict(zip(df['網頁名稱'], df['網址']))
        selected_name = st.selectbox("選擇要查看的網頁節點", df['網頁名稱'].tolist())
        selected_url = name_to_url[selected_name]

        # 選擇圖表類型
        chart_type = st.radio("選擇視覺化圖表類型：", ["直方圖 (Bar)", "折線圖 (Line)", "圓餅圖 (Pie)"], horizontal=True)

        successors = list(G.successors(selected_url))
        if successors:
            sub_df = df[df['網址'].isin(successors)]
            
            if chart_type == "圓餅圖 (Pie)":
                fig = px.pie(sub_df, values='權重值', names='網頁名稱', hole=0.4, height=500)
            elif chart_type == "直方圖 (Bar)":
                fig = px.bar(sub_df, x='網頁名稱', y='權重值', color='權重值', color_continuous_scale='Blues', height=500)
            else:
                fig = px.line(sub_df, x='網頁名稱', y='權重值', markers=True, height=500)
                
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning(f"「{selected_name}」在當前拓樸結構中沒有發現下游指向連結。")

        st.divider()

        # 拓樸圖展示
        st.subheader("🕸️ 互動式網路拓樸圖 (顏色代表權威度級別)")
        st.markdown("<span style='color:#ef4444'>● 紅色：核心權威</span> | <span style='color:#a855f7'>● 紫色：高影響力</span> | <span style='color:#3b82f6'>● 藍色：一般節點</span>", unsafe_allow_html=True)
        html_content = generate_network_html(G, data["scores"])
        components.html(html_content, height=650)
    else:
        st.info("💡 請在左側設定參數並點擊『開始分析』以生成數據圖表。")


# --- TAB 2: 【增加 2】INSTAGRAM 假帳號 PR 識別區 ---
with tab2:
    st.header("🛡️ 基於 PageRank 網絡邏輯的社交媒體模擬假帳號識別")
    st.markdown("""
    ### 💡 核心判定邏輯說明
    在社交網路上，**假帳號（機器人）**與**真實使用者/名人**的引薦網路結構有著顯著的 PageRank 差異：
    * **正常名人/權威**：擁有極高的 **入站連線（被大量人追蹤）**，PageRank 分數極高。
    * **互粉集團/假帳號**：它們會自建一個「封閉式的互相追蹤網路」來刷粉絲數。雖然它們互相追蹤，但**極少有外部真實高權重的帳號去追蹤它們**，這會導致它們的 PageRank 值異常低，或結構集中度反常。
    """)
    
    st.subheader("🤖 模擬社交帳號測謊儀")
    st.write("輸入一個虛擬的網路上追蹤數據，來判定該帳號集體是否為「自動化互粉假帳號集團」：")
    
    # 讓使用者在介面建立一個簡單的追蹤關係來進行 PR 計算
    test_relations = st.text_area(
        "請輸入帳號追蹤關係 (格式：追蹤者->被追蹤者，每行一筆)", 
        value="User_A->Celebrity_Real\nUser_B->Celebrity_Real\nBot_1->Bot_2\nBot_2->Bot_3\nBot_3->Bot_1\nBot_1->Celebrity_Real"
    )
    
    if st.button("執行社交測謊分析", type="secondary"):
        sim_G = nx.DiGraph()
        try:
            for line in test_relations.strip().split('\n'):
                if "->" in line:
                    u, v = line.split("->")
                    sim_G.add_edge(u.strip(), v.strip())
            
            sim_scores = nx.pagerank(sim_G, alpha=0.85)
            sim_df = pd.DataFrame(list(sim_scores.items()), columns=['帳號名稱', 'PR信用權重']).sort_values(by='PR信用權重', ascending=False)
            
            c1, c2 = st.columns([4, 6])
            with c1:
                st.write("#### 📥 帳號網路 PR 信用分")
                st.dataframe(sim_df, use_container_width=True, hide_index=True)
            
            with c2:
                st.write("#### 🔍 系統自動化稽核報告")
                for index, row in sim_df.iterrows():
                    name = row['帳號名稱']
                    score = row['PR信用權重']
                    
                    # 邏輯：入度大但出度為0的名人為真；互粉圈內且分數被稀釋的判定為疑似機器人
                    in_deg = sim_G.in_degree(name)
                    out_deg = sim_G.out_degree(name)
                    
                    if "Bot" in name or (out_deg > 0 and in_deg > 0 and score < 1/sim_G.number_of_nodes()):
                        st.error(f"❌ 異常帳號：【{name}】-> 判定為【疑似互粉集團假帳號】。原因：參與封閉互粉迴圈，且 PageRank 權重過低。")
                    elif in_deg > 2:
                        st.success(f"✅ 權威帳號：【{name}】-> 判定為【真實具影響力帳號】。原因：獲得廣泛單向追蹤。")
                    else:
                        st.info(f"⚪ 普通帳號：【{name}】-> 判定為【一般乾淨使用者】。")
        except Exception as e:
            st.error(f"輸入格式有誤，請確保使用 `->` 作為分隔符號。錯誤訊息：{e}")
