import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import requests
from streamlit_geolocation import streamlit_geolocation
from openrouteservice import client

# ==========================================================
# API SETUP
# ==========================================================
ORS_API_KEY = 'eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjAyM2M5MjE3ODIxNzRkY2FiMDNkZWI0OGZiN2M3Y2ZlIiwiaCI6Im11cm11cjY0In0=' 
ors_client = client.Client(key=ORS_API_KEY)

# SA GOVT API SETUP
SA_FUEL_TOKEN = 'cfba60f1-ddea-4fc0-8889-832a414aafc9'

st.set_page_config(page_title="Smart Fuel Finder", layout="centered")

VEHICLE_TYPES = {
    "Small Car (Hatch/Sedan)": 6.5,
    "Medium Car (SUV/Wagon)": 8.0,
    "Large SUV / 4WD": 11.5,
    "Ute / Van (Diesel)": 9.0,
    "Performance / V8": 14.5,
    "Hybrid": 4.2,
    "Custom Number": 0.0
}

if 'center' not in st.session_state: 
    st.session_state.center = [-34.9285, 138.6007]
if 'zoom' not in st.session_state: 
    st.session_state.zoom = 12
if 'selected_servos' not in st.session_state: 
    st.session_state.selected_servos = []
if 'user_loc' not in st.session_state: 
    st.session_state.user_loc = None
if 'viewed_servo' not in st.session_state: 
    st.session_state.viewed_servo = None

# ==========================================================
# LIVE DATA LOGIC
# ==========================================================
@st.cache_data(ttl=900)
def fetch_live_sa_prices(token):
    if token == 'YOUR_DATA_PUBLISHER_TOKEN_HERE':
        st.error("⚠️ Please paste your SA Government Token in the code!")
        return pd.DataFrame()
        
    headers = {
        "Authorization": f"FPDAPI SubscriberToken={token}",
        "Content-Type": "application/json"
    }
    
    try:
        # 1. Fetch Fuel Types
        ft_url = "https://fppdirectapi-prod.safuelpricinginformation.com.au/Subscriber/GetCountryFuelTypes?countryId=21"
        ft_res = requests.get(ft_url, headers=headers)
        
        if ft_res.status_code != 200:
            st.error(f"Govt Server Error (Fuel Types): {ft_res.status_code} - {ft_res.text}")
            return pd.DataFrame()

        fuel_mapping = {}
        for item in ft_res.json():
            name = item.get("Name", "").upper()
            if "91" in name and "E10" not in name: fuel_mapping[item["FuelId"]] = "price_U91"
            elif "95" in name: fuel_mapping[item["FuelId"]] = "price_U95"
            elif "98" in name: fuel_mapping[item["FuelId"]] = "price_U98"
            elif "DIESEL" in name and "PREMIUM" not in name: fuel_mapping[item["FuelId"]] = "price_Diesel"

        # 2. Fetch Sites
        sites_url = "https://fppdirectapi-prod.safuelpricinginformation.com.au/Subscriber/GetFullSiteDetails?countryId=21&geoRegionLevel=3&geoRegionId=4"
        sites_res = requests.get(sites_url, headers=headers)
        
        if sites_res.status_code != 200:
            st.error(f"Govt Server Error (Sites): {sites_res.status_code} - {sites_res.text}")
            return pd.DataFrame()

        sites_data = sites_res.json()
        sites_list = sites_data.get("S", sites_data) if isinstance(sites_data, dict) else sites_data
        sites_df = pd.DataFrame(sites_list)
        sites_df = sites_df.rename(columns={"S": "SiteId", "N": "name", "Lat": "lat", "Lng": "lon"})
        
        # 3. Fetch Prices
        prices_url = "https://fppdirectapi-prod.safuelpricinginformation.com.au/Price/GetSitesPrices?countryId=21&geoRegionLevel=3&geoRegionId=4"
        prices_res = requests.get(prices_url, headers=headers)
        
        if prices_res.status_code != 200:
            st.error(f"Govt Server Error (Prices): {prices_res.status_code} - {prices_res.text}")
            return pd.DataFrame()

        prices_data = prices_res.json()
        prices_list = prices_data if isinstance(prices_data, list) else prices_data.get("SitePrices", prices_data)
        prices_df = pd.DataFrame(prices_list)
        
        # 4. Clean and Merge
        prices_df = prices_df[prices_df['Price'] != 9999.0]
        prices_df['Price'] = prices_df['Price'] / 1000.0
        prices_df['FuelType'] = prices_df['FuelId'].map(fuel_mapping)
        prices_df = prices_df.dropna(subset=['FuelType']) 
        
        pivot_prices = prices_df.pivot(index='SiteId', columns='FuelType', values='Price').reset_index()
        final_df = pd.merge(sites_df, pivot_prices, on='SiteId', how='inner')
        
        for f in ['price_U91', 'price_U95', 'price_U98', 'price_Diesel']:
            if f not in final_df.columns: 
                final_df[f] = 0.00
        
        return final_df.fillna(0.00)
        
    except Exception as e:
        st.error(f"App Error: {e}")
        return pd.DataFrame()

stations_df = fetch_live_sa_prices(SA_FUEL_TOKEN)

def get_matrix_results(u_lon, u_lat, dataframe, eff, litres, return_trip):
    all_coords = [[u_lon, u_lat]] + dataframe[['lon', 'lat']].values.tolist()
    try:
        matrix = ors_client.distance_matrix(
            locations=all_coords, sources=[0],
            destinations=list(range(1, len(all_coords))),
            profile='driving-car', metrics=['duration', 'distance']
        )
        results = []
        multiplier = 2 if return_trip else 1
        for i, (index, row) in enumerate(dataframe.iterrows()):
            d_km = (matrix['distances'][0][i] / 1000) * multiplier
            t_min = (matrix['durations'][0][i] / 60) * multiplier
            trip_fuel_cost = (d_km * (eff / 100)) * row['current_price']
            total = (litres * row['current_price']) + trip_fuel_cost
            results.append({
                "Station": row['name'], "Total Trip Cost": round(total, 2),
                "Drive Time": round(t_min, 1), "Pump Price": f"${row['current_price']:.2f}",
                "Dist (km)": round(d_km, 2)
            })
        return pd.DataFrame(results).sort_values("Total Trip Cost")
    except: 
        return None

# ==========================================================
# UI
# ==========================================================
st.title("Smart Fuel Finder")

with st.expander("📖 How to use Smart Fuel Finder", expanded=True):
    st.markdown("""
    **1. Set Location:** Type your suburb in the search bar and press enter, OR click the **Target Icon** to use your phone's GPS.
    **2. Pick Fuel:** Select what your car drinks.
    **3. Explore Map:** Tap any colored pin to view current prices.
    **4. Calculate:** Found a servo you like? Tap the **"➕ Add to Calculator"** button under the map. Add a second one to compare!
    """)

st.markdown("### 1. Where are you?")
col1, col2 = st.columns([4, 1])
with col1:
    manual_address = st.text_input("Search Location", placeholder="e.g. Marion SA", label_visibility="collapsed")
with col2:
    loc = streamlit_geolocation()

st.markdown("### 2. What fuel do you need?")
fuel_choice = st.selectbox("Select Fuel Type", options=["U91", "U95", "U98", "Diesel"], label_visibility="collapsed")

if manual_address:
    try:
        geocode_res = ors_client.pelias_search(text=f"{manual_address}, South Australia")
        if geocode_res and 'features' in geocode_res and len(geocode_res['features']) > 0:
            coords = geocode_res['features'][0]['geometry']['coordinates']
            st.session_state.user_loc = [coords[0], coords[1]]
            st.session_state.center = [coords[1], coords[0]]
            st.success(f"📍 Location locked: {geocode_res['features'][0]['properties']['label']}")
    except: 
        pass
elif loc and loc.get('latitude'):
    st.session_state.user_loc = [loc['longitude'], loc['latitude']]
    st.session_state.center = [loc['latitude'], loc['longitude']]
    st.success("📍 GPS Location locked!")

if not stations_df.empty:
    stations_df['current_price'] = stations_df[f'price_{fuel_choice}']
    stations_df = stations_df[stations_df['current_price'] > 0.0]

st.markdown("### 3. Tap a pin to explore")

if stations_df.empty:
    st.warning("Loading Live SA Government Data... (or no stations sell this fuel nearby).")
else:
    m = folium.Map(location=st.session_state.center, zoom_start=st.session_state.zoom)

    if st.session_state.user_loc:
        folium.Marker(
            [st.session_state.user_loc[1], st.session_state.user_loc[0]], 
            icon=folium.Icon(color='black', icon='info-sign'),
            popup="Start Location"
        ).add_to(m)

    for _, row in stations_df.iterrows():
        is_sel = row['name'] in st.session_state.selected_servos
        color = "black" if is_sel else ("#28a745" if row['current_price'] < 1.90 else "#dc3545")
        
        popup_html = f"""
        <div style='min-width: 120px; font-family: sans-serif;'>
            <b style='font-size: 14px;'>{row['name']}</b><br>
            <hr style='margin: 4px 0;'>
            U91: ${row['price_U91']:.2f}<br>
            U95: ${row['price_U95']:.2f}<br>
            U98: ${row['price_U98']:.2f}<br>
            Diesel: ${row['price_Diesel']:.2f}
        </div>
        """
        
        folium.Marker(
            [row['lat'], row['lon']],
            icon=folium.DivIcon(html=f"""<div style="color:white; background:{color}; padding:5px; border-radius:4px; 
                border:1px solid black; width:50px; text-align:center; font-weight:bold;">${row['current_price']:.2f}</div>"""),
            popup=folium.Popup(popup_html, max_width=250)
        ).add_to(m)

    st_data = st_folium(m, center=st.session_state.center, zoom=st.session_state.zoom, use_container_width=True, height=450, key="map")

    if st_data and st_data.get("center"):
        st.session_state.center = [st_data["center"]["lat"], st_data["center"]["lng"]]
        st.session_state.zoom = st_data["zoom"]

    if st_data and st_data.get('last_object_clicked'):
        clicked_lat = round(st_data['last_object_clicked']['lat'], 4)
        clicked_lon = round(st_data['last_object_clicked']['lng'], 4)
        stations_df['lat_r'] = stations_df['lat'].round(4)
        stations_df['lon_r'] = stations_df['lon'].round(4)
        match = stations_df[(stations_df['lat_r'] == clicked_lat) & (stations_df['lon_r'] == clicked_lon)]
        if not match.empty:
            st.session_state.viewed_servo = match.iloc[0]['name']

if st.session_state.viewed_servo:
    if st.session_state.viewed_servo not in st.session_state.selected_servos:
        if st.button(f"➕ Add {st.session_state.viewed_servo} to Calculator", use_container_width=True):
            st.session_state.selected_servos.append(st.session_state.viewed_servo)
            if len(st.session_state.selected_servos) > 2:
                st.session_state.selected_servos.pop(0)
            st.rerun()
    else:
        st.success(f"✅ {st.session_state.viewed_servo} is locked in below.")

st.divider()

if st.session_state.user_loc and st.session_state.selected_servos:
    st.markdown("### 4. Calculator")
    with st.expander("⚙️ Adjust Car Settings", expanded=False):
        v_type = st.selectbox("Vehicle Type", options=list(VEHICLE_TYPES.keys()))
        eff = VEHICLE_TYPES[v_type] if v_type != "Custom Number" else st.number_input("L/100km", value=8.5)
        litres = st.slider("Refuel Amount (L)", 10, 150, 50)
        return_trip = st.toggle("Include Return Trip", value=True)

    u_lon, u_lat = st.session_state.user_loc
    picked_df = stations_df[stations_df['name'].isin(st.session_state.selected_servos)]
    res_df = get_matrix_results(u_lon, u_lat, picked_df, eff, litres, return_trip)
    
    if res_df is not None and not res_df.empty:
        if len(st.session_state.selected_servos) == 1:
            item = res_df.iloc[0]
            st.subheader(f"📍 {item['Station']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Cost", f"${item['Total Trip Cost']}")
            c2.metric("Drive Time", f"{item['Drive Time']}m")
            c3.metric("Distance", f"{item['Dist (km)']}km")
            st.info("Tap another servo on the map and click 'Add' to compare them.")
        
        elif len(st.session_state.selected_servos) == 2:
            winner = res_df.iloc[0]
            loser = res_df.iloc[1]
            savings = round(loser['Total Trip Cost'] - winner['Total Trip Cost'], 2)
            time_diff = round(abs(winner['Drive Time'] - loser['Drive Time']), 1)
            
            st.success(f"🏆 {winner['Station']} is your best value.")
            if winner['Drive Time'] > loser['Drive Time']:
                st.warning(f"Trade-off: Saving ${savings} will cost you an extra {time_diff} mins of driving.")
                
            col_a, col_b = st.columns(2)
            for i, row in res_df.iterrows():
                with [col_a, col_b][i]:
                    st.metric(row['Station'], f"${row['Total Trip Cost']}")
                    st.caption(f"Drive: {row['Drive Time']}m | Dist: {row['Dist (km)']}km")

if len(st.session_state.selected_servos) > 0:
    if st.button("Clear Selections", use_container_width=True):
        st.session_state.selected_servos = []
        st.session_state.viewed_servo = None
        st.rerun()
