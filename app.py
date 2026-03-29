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

st.set_page_config(page_title="Smart Fuel Finder", layout="centered")

# Session State for Mobile Persistence
if 'center' not in st.session_state: st.session_state.center = [-34.9285, 138.6007]
if 'zoom' not in st.session_state: st.session_state.zoom = 12
if 'selected_servos' not in st.session_state: st.session_state.selected_servos = []
if 'user_loc' not in st.session_state: st.session_state.user_loc = None

@st.cache_data(ttl=600)
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
        return pd.merge(sites_df, pivot_prices, on='SiteId', how='inner').fillna(0.00)
    except: return pd.DataFrame()

df = fetch_live_sa_prices(SA_FUEL_TOKEN)

# ==========================================================
# UI - MOBILE OPTIMIZED
# ==========================================================
st.title("⛽ Smart Fuel Finder")

# 1. TOP CONTROLS (Expanders save vertical space on mobile)
with st.expander("📍 Set Location & Fuel", expanded=True):
    fuel_choice = st.selectbox("Fuel Type", ["U91", "U95", "U98", "Diesel"])
    fuel_col = f"price_{fuel_choice}"
    
    col_srch, col_gps = st.columns([4,1])
    with col_srch:
        addr = st.text_input("Search Suburb", placeholder="e.g. Marion")
    with col_gps:
        st.write("") # Alignment
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

# 2. THE SMART "BEST VALUE" LIST
if not df.empty and fuel_col in df.columns:
    active_df = df[df[fuel_col] > 0].copy()
    
    # Color logic
    prices = active_df[fuel_col].values
    low_thresh = np.percentile(prices, 25)
    high_thresh = np.percentile(prices, 75)

    def get_color(p):
        if p <= low_thresh: return "#28a745" # Green
        if p >= high_thresh: return "#dc3545" # Red
        return "#ffc107" # Yellow

    # Filter to nearby (15km) for the "Best Deals" list
    u_lon, u_lat = st.session_state.center[1], st.session_state.center[0]
    active_df['dist_km'] = np.sqrt((active_df['lon']-u_lon)**2 + (active_df['lat']-u_lat)**2) * 111
    
    nearby_best = active_df[active_df['dist_km'] < 15].sort_values(fuel_col).head(5)

    st.subheader(f"🏆 Best {fuel_choice} Deals Nearby")
    if nearby_best.empty:
        st.info("Move the map or search a suburb to find local deals.")
    else:
        for idx, row in nearby_best.iterrows():
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**{row['name']}** \n${row[fuel_col]:.2f} ({row['dist_km']:.1f}km away)")
            with c2:
                if st.button("➕ Add", key=f"btn_{row['SiteId']}"):
                    if row['name'] not in st.session_state.selected_servos:
                        st.session_state.selected_servos.append(row['name'])
                    st.rerun()

    # 3. THE MAP
    st.divider()
    m = folium.Map(location=st.session_state.center, zoom_start=st.session_state.zoom, control_scale=True)
    
    # Only render what's near the center to keep mobile snappy
    visible_df = active_df[active_df['dist_km'] < 20]
    
    for _, row in visible_df.iterrows():
        color = get_color(row[fuel_col])
        folium.Marker(
            [row['lat'], row['lon']],
            icon=folium.DivIcon(html=f"""<div style="background:{color}; color:white; padding:2px; border-radius:3px; font-weight:bold; border:1px solid black; width:45px; text-align:center; font-size:12px;">${row[fuel_col]:.2f}</div>"""),
            popup=row['name']
        ).add_to(m)

    st_map = st_folium(m, center=st.session_state.center, zoom=st.session_state.zoom, height=400, use_container_width=True)
    
    if st_map.get("center"):
        st.session_state.center = [st_map["center"]["lat"], st_map["center"]["lng"]]
        st.session_state.zoom = st_map["zoom"]

    # 4. CALCULATOR (At the bottom)
    if st.session_state.selected_servos:
        st.divider()
        st.subheader("⚖️ Comparison")
        calc_df = active_df[active_df['name'].isin(st.session_state.selected_servos)]
        
        with st.expander("Vehicle Settings"):
            litres = st.slider("Fill amount (L)", 10, 100, 50)
            eff = st.number_input("Car L/100km", value=8.5)

        for _, row in calc_df.iterrows():
            cost = row[fuel_col] * litres
            st.info(f"**{row['name']}**: Total ${cost:.2f}")
        
        if st.button("Clear All", use_container_width=True):
            st.session_state.selected_servos = []
            st.rerun()
