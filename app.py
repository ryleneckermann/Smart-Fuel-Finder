import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import requests
from streamlit_geolocation import streamlit_geolocation
from openrouteservice import client
import numpy as np

# ==========================================================
# API SETUP
# ==========================================================
ORS_API_KEY = 'eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjAyM2M5MjE3ODIxNzRkY2FiMDNkZWI0OGZiN2M3Y2ZlIiwiaCI6Im11cm11cjY0In0=' 
ors_client = client.Client(key=ORS_API_KEY)
SA_FUEL_TOKEN = 'cfba60f1-ddea-4fc0-8889-832a414aafc9'

st.set_page_config(page_title="Smart Fuel Finder", layout="wide")

# Session State
if 'center' not in st.session_state: st.session_state.center = [-34.9285, 138.6007]
if 'zoom' not in st.session_state: st.session_state.zoom = 12
if 'selected_servos' not in st.session_state: st.session_state.selected_servos = []
if 'user_loc' not in st.session_state: st.session_state.user_loc = None

@st.cache_data(ttl=900)
def fetch_live_sa_prices(token):
    headers = {"Authorization": f"FPDAPI SubscriberToken={token}", "Content-Type": "application/json"}
    try:
        ft_res = requests.get("https://fppdirectapi-prod.safuelpricinginformation.com.au/Subscriber/GetCountryFuelTypes?countryId=21", headers=headers)
        ft_list = ft_res.json().get('Fuels', [])
        fuel_mapping = {2: "price_U91", 5: "price_U95", 8: "price_U98", 3: "price_Diesel"}
        
        sites_res = requests.get("https://fppdirectapi-prod.safuelpricinginformation.com.au/Subscriber/GetFullSiteDetails?countryId=21&geoRegionLevel=3&geoRegionId=4", headers=headers)
        sites_df = pd.DataFrame(sites_res.json().get('S', []))
        sites_df = sites_df.rename(columns={"S": "SiteId", "N": "name", "Lat": "lat", "Lng": "lon"})
        
        prices_res = requests.get("https://fppdirectapi-prod.safuelpricinginformation.com.au/Price/GetSitesPrices?countryId=21&geoRegionLevel=3&geoRegionId=4", headers=headers)
        prices_df = pd.DataFrame(prices_res.json())
        prices_df = prices_df[prices_df['Price'] != 9999.0]
        prices_df['Price'] = prices_df['Price'] / 1000.0
        prices_df['FuelType'] = prices_df['FuelId'].map(fuel_mapping)
        prices_df = prices_df.dropna(subset=['FuelType'])
        
        pivot_prices = prices_df.pivot(index='SiteId', columns='FuelType', values='Price').reset_index()
        final_df = pd.merge(sites_df, pivot_prices, on='SiteId', how='inner')
        return final_df.fillna(0.00)
    except: return pd.DataFrame()

# Load Data
df = fetch_live_sa_prices(SA_FUEL_TOKEN)

st.title("⛽ Smart Fuel Finder")

# 1. SETTINGS & FILTERS
with st.sidebar:
    st.header("Settings")
    fuel_choice = st.selectbox("Fuel Type", ["U91", "U95", "U98", "Diesel"])
    fuel_col = f"price_{fuel_choice}"
    
    st.divider()
    st.subheader("Calculator Settings")
    litres = st.slider("Fill amount (L)", 10, 100, 50)
    eff = st.number_input("Car L/100km", value=8.5)

# 2. LOCATION SEARCH
col_srch, col_gps = st.columns([4,1])
with col_srch:
    addr = st.text_input("Search Suburb", placeholder="e.g. Glenelg")
with col_gps:
    loc = streamlit_geolocation()

if addr:
    try:
        geo = ors_client.pelias_search(text=f"{addr}, SA")['features'][0]['geometry']['coordinates']
        st.session_state.user_loc = [geo[0], geo[1]]
        st.session_state.center = [geo[1], geo[0]]
    except: pass
elif loc and loc.get('latitude'):
    st.session_state.user_loc = [loc['longitude'], loc['latitude']]
    st.session_state.center = [loc['latitude'], loc['longitude']]

# 3. DATA PROCESSING
if not df.empty and fuel_col in df.columns:
    active_df = df[df[fuel_col] > 0].copy()
    
    # Calculate "Cheapness" colors
    low, high = active_df[fuel_col].quantile([0.2, 0.8])
    def get_color(p):
        if p <= low: return "#28a745" # Green
        if p >= high: return "#dc3545" # Red
        return "#ffc107" # Yellow/Orange

    # 4. BEST VALUE TABLE (TOP 5)
    # If we have location, sort by distance. Otherwise, sort by price.
    st.subheader(f"🏆 Best {fuel_choice} Deals Nearby")
    if st.session_state.user_loc:
        u_lon, u_lat = st.session_state.user_loc
        active_df['dist'] = np.sqrt((active_df['lon']-u_lon)**2 + (active_df['lat']-u_lat)**2) * 111
        top_5 = active_df.sort_values(fuel_col).head(5)
    else:
        top_5 = active_df.sort_values(fuel_col).head(5)
    
    cols = st.columns(5)
    for i, (idx, row) in enumerate(top_5.iterrows()):
        with cols[i]:
            st.metric(row['name'][:15], f"${row[fuel_col]:.2f}")
            if st.button(f"Add #{i+1}", key=f"add_{i}"):
                if row['name'] not in st.session_state.selected_servos:
                    st.session_state.selected_servos.append(row['name'])
                    st.rerun()

    # 5. THE MAP
    st.divider()
    m = folium.Map(location=st.session_state.center, zoom_start=st.session_state.zoom)
    
    # Only show stations in the current map area to keep it fast
    for _, row in active_df.iterrows():
        # Check if in "viewport" (rough estimate)
        if abs(row['lat'] - st.session_state.center[0]) < 0.2 and abs(row['lon'] - st.session_state.center[1]) < 0.2:
            color = get_color(row[fuel_col])
            folium.Marker(
                [row['lat'], row['lon']],
                icon=folium.DivIcon(html=f"""<div style="background:{color}; color:white; padding:3px; border-radius:3px; font-weight:bold; border:1px solid black; width:45px; text-align:center;">${row[fuel_col]:.2f}</div>"""),
                popup=f"<b>{row['name']}</b><br>Click 'Add' in the table above to compare."
            ).add_to(m)

    st_map = st_folium(m, center=st.session_state.center, zoom=st.session_state.zoom, height=500, use_container_width=True)
    
    # 6. CALCULATOR (HIDDEN UNTIL USED)
    if st.session_state.selected_servos:
        st.divider()
        st.subheader("⚖️ Fuel Comparison Calculator")
        calc_df = active_df[active_df['name'].isin(st.session_state.selected_servos)]
        
        for _, row in calc_df.iterrows():
            with st.expander(f"Analysis for {row['name']}", expanded=True):
                price = row[fuel_col]
                total_fuel = price * litres
                st.write(f"Pump Price: **${price:.2f}**")
                st.write(f"Total for {litres}L: **${total_fuel:.2f}**")
        
        if st.button("Clear Selections"):
            st.session_state.selected_servos = []
            st.rerun()
else:
    st.info("Searching for the best fuel prices in SA...")
