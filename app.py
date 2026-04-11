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
ORS_API_KEY = 'eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjAyM2M5MjE3ODIxNzRkY2FiMDNkZWI0OGZiN2M3Y2ZlIiwiaCI6Im11cm11cjY0In0=' # Replace this securely
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

# ==========================================================
# SESSION STATE
# ==========================================================
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
if 'auto_winners' not in st.session_state:
    st.session_state.auto_winners = None

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================
def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculates straight-line distance between two points on earth in km."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    return R * c

@st.cache_data(ttl=900)
def fetch_live_sa_prices(token):
    headers = {
        "Authorization": f"FPDAPI SubscriberToken={token}",
        "Content-Type": "application/json"
    }
    try:
        ft_res = requests.get("https://fppdirectapi-prod.safuelpricinginformation.com.au/Subscriber/GetCountryFuelTypes?countryId=21", headers=headers)
        ft_json = ft_res.json()
        ft_list = ft_json.get('Fuels', []) if isinstance(ft_json, dict) else ft_json
        
        fuel_mapping = {}
        for item in ft_list:
            fid = item.get("FuelId")
            if fid == 2: fuel_mapping[fid] = "price_U91"   
            elif fid == 5: fuel_mapping[fid] = "price_U95" 
            elif fid == 8: fuel_mapping[fid] = "price_U98" 
            elif fid == 3: fuel_mapping[fid] = "price_Diesel" 

        sites_res = requests.get("https://fppdirectapi-prod.safuelpricinginformation.com.au/Subscriber/GetFullSiteDetails?countryId=21&geoRegionLevel=3&geoRegionId=4", headers=headers)
        sites_json = sites_res.json()
        sites_list = sites_json.get('S', []) if isinstance(sites_json, dict) else sites_json
        
        if not sites_list: return pd.DataFrame()

        sites_df = pd.DataFrame(sites_list)
        sites_df = sites_df.rename(columns={"S": "SiteId", "N": "name", "Lat": "lat", "Lng": "lon"})
        
        prices_res = requests.get("https://fppdirectapi-prod.safuelpricinginformation.com.au/Price/GetSitesPrices?countryId=21&geoRegionLevel=3&geoRegionId=4", headers=headers)
        prices_list = prices_res.json()
        if not isinstance(prices_list, list):
            prices_list = prices_list.get('SitePrices', []) if isinstance(prices_list, dict) else []

        prices_df = pd.DataFrame(prices_list)
        prices_df = prices_df[prices_df['Price'] != 9999.0]
        # We divide by 1000 here, preserving max decimal precision in the dataframe
        prices_df['Price'] = prices_df['Price'] / 1000.0 
        prices_df['FuelType'] = prices_df['FuelId'].map(fuel_mapping)
        prices_df = prices_df.dropna(subset=['FuelType']) 
        
        pivot_prices = prices_df.pivot(index='SiteId', columns='FuelType', values='Price').reset_index()
        final_df = pd.merge(sites_df, pivot_prices, on='SiteId', how='inner')
        
        for f in ['price_U91', 'price_U95', 'price_U98', 'price_Diesel']:
            if f not in final_df.columns: final_df[f] = 0.00
        
        return final_df.fillna(0.00)
    except Exception as e:
        st.error(f"Error fetching data: {e}")
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
            
            # Math utilizes the unrounded max precision 'current_price' float
            trip_fuel_cost = (d_km * (eff / 100)) * row['current_price']
            total = (litres * row['current_price']) + trip_fuel_cost
            
            results.append({
                "Station": row['name'], 
                "Total Trip Cost": total, 
                "Pump Price": f"${row['current_price']:.3f}", 
                "Drive Time": f"{round(t_min, 1)}m", 
                "Dist (km)": f"{round(d_km, 2)}km", 
                "Lat": row['lat'], 
                "Lon": row['lon'],
                "Cost Display": f"${total:.2f}" 
            })
        
        res_df = pd.DataFrame(results).sort_values("Total Trip Cost")
        res_df = res_df.drop(columns=["Total Trip Cost"]).rename(columns={"Cost Display": "Total Trip Cost"})
        return res_df
    except Exception as e: 
        st.error(f"Routing API Error: Make sure API limits aren't exceeded. {e}")
        return None

# ==========================================================
# UI: TOP BAR
# ==========================================================
st.title("Smart Fuel Finder")

col1, col2, col3 = st.columns([3, 1, 2])
with col1:
    manual_address = st.text_input("Search Loc", placeholder="e.g. Marion SA", label_visibility="collapsed")
with col2:
    loc = streamlit_geolocation()
with col3:
    fuel_choice = st.selectbox("Fuel Type", options=["U91", "U95", "U98", "Diesel"], label_visibility="collapsed")

# Handle Location Updates
if manual_address:
    try:
        geocode_res = ors_client.pelias_search(text=f"{manual_address}, South Australia")
        if geocode_res and 'features' in geocode_res and len(geocode_res['features']) > 0:
            coords = geocode_res['features'][0]['geometry']['coordinates']
            st.session_state.user_loc = [coords[0], coords[1]]
            st.session_state.center = [coords[1], coords[0]]
            st.success(f"📍 Location locked: {geocode_res['features'][0]['properties']['label']}")
    except: pass
elif loc and loc.get('latitude'):
    st.session_state.user_loc = [loc['longitude'], loc['latitude']]
    st.session_state.center = [loc['latitude'], loc['longitude']]
    st.success("📍 GPS Location locked!")

# Clean Data & Calculate Base Distances
if not stations_df.empty:
    stations_df['current_price'] = stations_df[f'price_{fuel_choice}']
    stations_df = stations_df[stations_df['current_price'] > 0.0]
    
    if st.session_state.user_loc:
        u_lon, u_lat = st.session_state.user_loc
        stations_df['dist_km'] = haversine_distance(u_lat, u_lon, stations_df['lat'], stations_df['lon'])

# ==========================================================
# UI: THE MAP (Front and Center)
# ==========================================================
if stations_df.empty:
    st.warning("Loading Live Data... (or no stations sell this fuel nearby).")
else:
    m = folium.Map(location=st.session_state.center, zoom_start=st.session_state.zoom)

    if st.session_state.user_loc:
        folium.Marker(
            [st.session_state.user_loc[1], st.session_state.user_loc[0]], 
            icon=folium.Icon(color='black', icon='info-sign'),
            popup="Start Location"
        ).add_to(m)
        
        map_display_df = stations_df[stations_df['dist_km'] <= 25.0]
    else:
        map_display_df = stations_df

    for _, row in map_display_df.iterrows():
        is_sel = row['name'] in st.session_state.selected_servos
        
        is_in_top_5 = False
        if st.session_state.auto_winners is not None:
            is_in_top_5 = row['name'] in st.session_state.auto_winners['Station'].values

        color = "#28a745" if row['current_price'] < 1.90 else "#dc3545"
        if is_sel: color = "black"
        if is_in_top_5: color = "blue" 
        
        popup_html = f"""
        <div style='min-width: 120px; font-family: sans-serif;'>
            <b style='font-size: 14px;'>{row['name']}</b><br>
            <hr style='margin: 4px 0;'>
            U91: ${row['price_U91']:.3f}<br>
            U95: ${row['price_U95']:.3f}<br>
            U98: ${row['price_U98']:.3f}<br>
            Diesel: ${row['price_Diesel']:.3f}
        </div>
        """
        
        border_style = "border: 2px solid gold;" if is_in_top_5 else "border:1px solid black;"
        folium.Marker(
            [row['lat'], row['lon']],
            icon=folium.DivIcon(html=f"""<div style="color:white; background:{color}; padding:5px; border-radius:4px; 
                {border_style} width:55px; text-align:center; font-weight:bold; font-size: 13px;">${row['current_price']:.3f}</div>"""),
            popup=folium.Popup(popup_html, max_width=250)
        ).add_to(m)

    st_data = st_folium(m, center=st.session_state.center, zoom=st.session_state.zoom, use_container_width=True, height=400, key="map")

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

# ==========================================================
# UI: DYNAMIC COMPARE BUTTON (Directly Under Map)
# ==========================================================
if st.session_state.viewed_servo:
    if st.session_state.viewed_servo not in st.session_state.selected_servos:
        if st.button(f"➕ Add {st.session_state.viewed_servo} to Compare", use_container_width=True):
            st.session_state.selected_servos.append(st.session_state.viewed_servo)
            if len(st.session_state.selected_servos) > 2:
                st.session_state.selected_servos.pop(0)
            st.rerun()
    else:
        st.info(f"✅ {st.session_state.viewed_servo} is locked in for comparison below.")

# ==========================================================
# UI: SETTINGS & CALCULATORS
# ==========================================================
st.divider()

with st.expander("⚙️ Adjust Car & Trip Settings", expanded=False):
    v_type = st.selectbox("Vehicle Type", options=list(VEHICLE_TYPES.keys()))
    
    # ALWAYS display the number input. It defaults to the selected car's value, but allows manual edits.
    default_eff = float(VEHICLE_TYPES[v_type]) if v_type != "Custom Number" else 8.5
    eff = st.number_input("Fuel Economy (L/100km)", value=default_eff, step=0.1)
    
    litres = st.slider("Refuel Amount (L)", 10, 150, 50)
    return_trip = st.toggle("Include Return Trip", value=True)

# ----------------------------------------------------------
# AUTO CALCULATOR
# ----------------------------------------------------------
if st.session_state.user_loc:
    if st.button("🚀 Find Best Overall Price (15km Radius)", use_container_width=True, type="primary"):
        with st.spinner("Calculating actual driving costs for nearby stations..."):
            u_lon, u_lat = st.session_state.user_loc
            
            nearby = stations_df[stations_df['dist_km'] <= 15.0]
            
            if nearby.empty:
                st.warning("No stations found within 15km.")
            else:
                nearby = nearby.sort_values('dist_km').head(48)
                res_df = get_matrix_results(u_lon, u_lat, nearby, eff, litres, return_trip)
                
                if res_df is not None and not res_df.empty:
                    st.session_state.auto_winners = res_df.head(5) 
                    st.session_state.center = [st.session_state.auto_winners.iloc[0]['Lat'], st.session_state.auto_winners.iloc[0]['Lon']]

if st.session_state.auto_winners is not None:
    winner = st.session_state.auto_winners.iloc[0]
    st.success(f"🏆 **Ultimate Best Value:** {winner['Station']}")
    
    st.markdown("#### Top 5 Best Value Stations")
    display_df = st.session_state.auto_winners[['Station', 'Total Trip Cost', 'Pump Price', 'Drive Time', 'Dist (km)']]
    st.dataframe(display_df, hide_index=True, use_container_width=True)
    
    if st.button("Clear Best Results"):
        st.session_state.auto_winners = None
        st.rerun()

st.divider()

# ----------------------------------------------------------
# MANUAL CALCULATOR COMPARISON RESULTS
# ----------------------------------------------------------
if st.session_state.user_loc and st.session_state.selected_servos:
    st.markdown("### Manual Comparison")
    u_lon, u_lat = st.session_state.user_loc
    picked_df = stations_df[stations_df['name'].isin(st.session_state.selected_servos)]
    res_df = get_matrix_results(u_lon, u_lat, picked_df, eff, litres, return_trip)
    
    if res_df is not None and not res_df.empty:
        if len(st.session_state.selected_servos) == 1:
            item = res_df.iloc[0]
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Cost", f"{item['Total Trip Cost']}")
            c2.metric("Drive Time", item['Drive Time'])
            c3.metric("Distance", item['Dist (km)'])
        
        elif len(st.session_state.selected_servos) == 2:
            w_cost = float(res_df.iloc[0]['Total Trip Cost'].replace('$', ''))
            l_cost = float(res_df.iloc[1]['Total Trip Cost'].replace('$', ''))
            w_time = float(res_df.iloc[0]['Drive Time'].replace('m', ''))
            l_time = float(res_df.iloc[1]['Drive Time'].replace('m', ''))
            
            savings = round(l_cost - w_cost, 2)
            time_diff = round(abs(w_time - l_time), 1)
            
            st.success(f"🏆 {res_df.iloc[0]['Station']} beats {res_df.iloc[1]['Station']} by ${savings:.2f}.")
            if w_time > l_time:
                st.warning(f"Trade-off: Saving ${savings:.2f} costs an extra {time_diff} mins driving.")
                
            col_a, col_b = st.columns(2)
            for i, row in res_df.iterrows():
                with [col_a, col_b][i]:
                    st.metric(row['Station'], row['Total Trip Cost'])
                    st.caption(f"Drive: {row['Drive Time']} | Dist: {row['Dist (km)']}")

    if st.button("Clear Manual Comparisons", use_container_width=True):
        st.session_state.selected_servos = []
        st.session_state.viewed_servo = None
        st.rerun()
