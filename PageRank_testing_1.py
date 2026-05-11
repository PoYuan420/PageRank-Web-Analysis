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

# --- 1. 爬蟲核心邏輯 ---
def get_links(url, headers, max_links):
    links = set()
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                full_url = urljoin(url, a['href'])
                # 過濾：只抓 http/https，排除常見非網頁檔案
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
    
    # 第一層
    st.info(f"正在分析起始網頁: {start_url}")
    layer1_links = get_links(start_url, headers, max_per_layer)
    
    for link in layer1_links:
        G.add_edge(start_url, link)
        
    # 第二層
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(layer1_links)
    
    for i, link in enumerate(layer1_links):
        status_text.text(f"正在爬取進度 ({i+1}/{total}): {link[:60]}...")
        l2_links = get_links(link, headers, max_per_layer)
        for l2 in l2_links:
            G.add_edge(link, l2)
        progress_bar.progress((i + 1) / total)
        time.sleep(0.05)
        
    status_text.text("爬取完成！")
    return G

# --- 2. 視覺化工具：Pyvis 互動網路圖 ---
def generate_network_html(G, pagerank_dict):
    net = Network(height="500px", width="100%", bgcolor="#222222", font_color="white", directed=True)
    
    # 取得權重最大值用於標準化節點大小
    max_pr = max(pagerank_dict.values()) if pagerank_dict else 1
    
    for node in G.nodes():
        score = pagerank_dict.get(node, 0)
        # 設定節點大小與顏色
        size = (score / max_pr) * 50 + 10 
        net.add_node(node, label=urlparse(node).netloc, title=node, size=size, color="#4face6")
        
    for source, target in G.edges():
        net.add_edge(source, target, color="#666666")
        
    net.force_atlas_2based() # 使用物理模擬佈局
    return net.generate_html()

# --- 3. Streamlit 介面設定 ---
st.set_page_config(page_title="PageRank 影響力分析系統", layout="wide")
st.title("🌐 網頁權重與關聯分析儀表板")

with st.sidebar:
    st.header("🔍 分析設定")
    start_url = st.text_input("輸入起始網址", value="https://www.wikipedia.org")
    max_links = st.slider("每層最大抓取數", 5, 50, 15)
    alpha = st.slider("PageRank 阻尼係數 (Alpha)", 0.0, 1.0, 0.85)
    analyze_btn = st.button("開始分析", type="primary")
    st.divider()
    st.caption("註：分析層級為兩層，增加抓取數會延長運算時間。")

# Session State 初始化
if 'data' not in st.session_state:
    st.session_state.data = None

if analyze_btn:
    with st.spinner("系統正在構建網路圖並計算 PageRank..."):
        G = crawl_web(start_url, max_links)
        # 計算權重
        scores = nx.pagerank(G, alpha=alpha)
        # 轉為 DataFrame
        df = pd.DataFrame(list(scores.items()), columns=['網址', '權重值'])
        df = df.sort_values(by='權重值', ascending=False).reset_index(drop=True)
        
        st.session_state.data = {"G": G, "df": df, "scores": scores}

# --- 4. 結果呈現 ---
if st.session_state.data:
    data = st.session_state.data
    df = data["df"]
    G = data["G"]
    
    # 第一排：核心數據指標
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("總節點數量", G.number_of_nodes())
    c2.metric("總連結數量", G.number_of_edges())
    c3.metric("平均權重", f"{df['權重值'].mean():.5f}")
    c4.metric("最高影響力", f"{df['權重值'].max():.4f}")

    st.divider()

    # 第二排：圖表分析
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("📊 權重排名 Top 15")
        # 畫出橫向長條圖
        fig_bar = px.bar(df.head(15), x='權重值', y='網址', 
                         orientation='h', color='權重值',
                         color_continuous_scale='Viridis',
                         hover_data=['權重值'])
        fig_bar.update_layout(yaxis={'categoryorder':'total ascending'}, height=500)
        st.plotly_chart(fig_bar, use_container_width=True)

    with col_right:
        st.subheader("🎯 特定節點下遊分析")
        selected_site = st.selectbox("選擇網頁查看其傳遞對象", df['網址'].tolist())
        
        successors = list(G.successors(selected_site))
        if successors:
            sub_df = df[df['網址'].isin(successors)]
            fig_pie = px.pie(sub_df, values='權重值', names='網址', 
                             title=f"從該網頁流出的權重分配",
                             hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.warning("此網頁為末端節點，無對外連結。")

    # 第三排：互動式網路圖
    st.divider()
    st.subheader("🕸️ 全局關聯互動拓樸圖")
    st.caption("你可以使用滑鼠滾輪縮放，或拖拽節點查看關聯。節點越大代表 PageRank 權重越高。")
    
    html_content = generate_network_html(G, data["scores"])
    components.html(html_content, height=550)

    # 底部：完整資料下載
    st.divider()
    with st.expander("查看原始數據表格"):
        st.dataframe(df, use_container_width=True)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("下載分析報表 (CSV)", csv, "pagerank_report.csv", "text/csv")

else:
    # 初始歡迎畫面
    st.empty()
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.image("https://img.icons8.com/clouds/500/web.png", width=300)
        st.markdown("### 歡迎使用網頁分析系統\n請在左側輸入網址，我們將為您分析該網頁的網路結構與權重分佈。")
