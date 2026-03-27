import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
from streamlit_geolocation import streamlit_geolocation
from openrouteservice import client

# ==========================================================
# API SETUP
# ==========================================================
ORS_API_KEY = 'eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjAyM2M5MjE3ODIxNzRkY2FiMDNkZWI0OGZiN2M3Y2ZlIiwiaCI6Im11cm11cjY0In0=' 
ors_client = client.Client(key=ORS_API_KEY)

st.set_page_config(page_title="SmartFuel Finder", layout="centered")

VEHICLE_TYPES = {
    "Small Car (Hatch/Sedan)": 6.5,
    "Medium Car (SUV/Wagon)": 8.0,
    "Large SUV / 4WD": 11.5,
    "Ute / Van (Diesel)": 9.0,
    "Performance / V8": 14.5,
    "Hybrid": 4.2,
    "Custom Number": 0.0
}

if 'center' not in st.session_state: st.session_state.center = [-34.9285, 138.6007]
if 'zoom' not in st.session_state: st.session_state.zoom = 12
if 'selected_servos' not in st.session_state: st.session_state.selected_servos = []
if 'user_loc' not in st.session_state: st.session_state.user_loc = None

# ==========================================================
# CORE LOGIC & DUMMY DATA
# ==========================================================
def load_stations():
    # Generating fake SA data with all fuel types for testing
    data = {
        'name': ['Liberty Glenelg', 'OTR Marion', 'Coles Express Brighton', 'Ampol Somerton', 'X Convenience Novar'],
        'lat': [-34.9811, -35.0004, -35.0152, -34.9934, -34.9655],
        'lon': [138.5165, 138.5448, 138.5190, 138.5201, 138.5367],
        'price_U91': [1.85, 1.95, 1.92, 1.89, 1.83],
        'price_U95': [1.99, 2.09, 2.05, 2.03, 1.97],
        'price_U98': [2.08, 2.18, 2.15, 2.12, 2.06],
        'price_Diesel': [1.95, 2.05, 2.01, 1.99, 1.93]
    }
    return pd.DataFrame(data)

def get_matrix_results(u_lon, u_lat, dataframe, eff, litres, return_trip):
    subset = dataframe.head(49)
    all_coords = [[u_lon, u_lat]] + subset[['lon', 'lat']].values.tolist()
    
    try:
        matrix = ors_client.distance_matrix(
            locations=all_coords, sources=[0],
            destinations=list(range(1, len(all_coords))),
            profile='driving-car', metrics=['duration', 'distance']
        )
        
        results = []
        multiplier = 2 if return_trip else 1
        
        for i, (index, row) in enumerate(subset.iterrows()):
            d_km = (matrix['distances'][0][i] / 1000) * multiplier
            t_min = (matrix['durations'][0][i] / 60) * multiplier
            
            trip_fuel_cost = (d_km * (eff / 100)) * row['current_price']
            total = (litres * row['current_price']) + trip_fuel_cost
            
            results.append({
                "Station": row['name'],
                "Total Trip Cost": round(total, 2),
                "Drive Time": round(t_min, 1),
                "Pump Price": f"${row['current_price']:.2f}",
                "Dist (km)": round(d_km, 2)
            })
        return pd.DataFrame(results).sort_values("Total Trip Cost")
    except Exception as e:
        st.error(f"API Connection Error: {e}")
        return None

def geocode_address(address_text):
    try:
        res = ors_client.pelias_search(text=f"{address_text}, South Australia")
        if res and 'features' in res and len(res['features']) > 0:
            coords = res['features'][0]['geometry']['coordinates']
            return coords 
        return None
    except:
        return None

stations_df = load_stations()

st.title("SmartFuel Finder")

# ==========================================================
# TOP SEARCH BAR 
# ==========================================================
col1, col2 = st.columns([4, 1])
with col1:
    manual_address = st.text_input("Search", placeholder="Search Suburb (e.g., Glenelg)", label_visibility="collapsed")
with col2:
    loc = streamlit_geolocation()

if manual_address:
    coords = geocode_address(manual_address)
    if coords:
        st.session_state.user_loc = coords
        st.session_state.center = [coords[1], coords[0]]
        st.session_state.zoom = 13
    else:
        st.warning("Could not find that location.")
elif loc and loc.get('latitude'):
    st.session_state.user_loc = [loc['longitude'], loc['latitude']]
    st.session_state.center = [loc['latitude'], loc['longitude']]

# ==========================================================
# FUEL SELECTION & MAP
# ==========================================================
map_header_col1, map_header_col2 = st.columns([1, 1])
with map_header_col2:
    fuel_choice = st.selectbox(
        "Select Fuel Type", 
        options=["U91", "U95", "U98", "Diesel"], 
        label_visibility="collapsed"
    )

# Set the active price based on the dropdown
stations_df['current_price'] = stations_df[f'price_{fuel_choice}']

if not stations_df.empty:
    min_p = stations_df['current_price'].min()
    max_p = stations_df['current_price'].max()
    range_p = max_p - min_p
    cheap_threshold = min_p + (range_p * 0.33)
    expensive_threshold = max_p - (range_p * 0.33)

m = folium.Map(location=st.session_state.center, zoom_start=st.session_state.zoom)

if st.session_state.user_loc:
    folium.Marker(
        [st.session_state.user_loc[1], st.session_state.user_loc[0]], 
        icon=folium.Icon(color='black', icon='info-sign'),
        popup="Start Location"
    ).add_to(m)

for _, row in stations_df.iterrows():
    is_sel = row['name'] in st.session_state.selected_servos
    
    if is_sel:
        bg_color = "black"
        text_color = "white"
    elif row['current_price'] <= cheap_threshold:
        bg_color = "#28a745" 
        text_color = "white"
    elif row['current_price'] >= expensive_threshold:
        bg_color = "#dc3545" 
        text_color = "white"
    else:
        bg_color = "#ffc107" 
        text_color = "black"
        
    # Build the popup menu showing all prices
    all_prices_html = f"""
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
        icon=folium.DivIcon(html=f"""<div style="color:{text_color}; background:{bg_color}; padding:5px; border-radius:4px; 
            border:1px solid black; width:50px; text-align:center; font-weight:bold; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
            ${row['current_price']:.2f}</div>"""),
        popup=row['name'],
        tooltip=all_prices_html
    ).add_to(m)

st_data = st_folium(m, use_container_width=True, height=450, key="map", returned_objects=["last_object_clicked_popup", "center", "zoom"])

if st_data:
    if st_data.get("center"):
        st.session_state.center = [st_data["center"]["lat"], st_data["center"]["lng"]]
    if st_data.get("zoom"):
        st.session_state.zoom = st_data["zoom"]

if st_data and st_data.get('last_object_clicked_popup') and st_data['last_object_clicked_popup'] != "Start Location":
    clicked = st_data['last_object_clicked_popup']
    if clicked not in st.session_state.selected_servos:
        st.session_state.selected_servos.append(clicked)
        if len(st.session_state.selected_servos) > 2:
            st.session_state.selected_servos.pop(0)
        st.rerun()

# ==========================================================
# VEHICLE SETTINGS & MATH 
# ==========================================================
with st.expander("Vehicle Settings & Cost Calculator", expanded=False):
    v_type = st.selectbox("Vehicle Type", options=list(VEHICLE_TYPES.keys()))
    if v_type == "Custom Number":
        eff = st.number_input("Efficiency (L/100km)", value=8.5, step=0.1)
    else:
        eff = VEHICLE_TYPES[v_type]
        
    litres = st.slider("Refuel Amount (L)", 10, 150, 50)
    return_trip = st.toggle("Include Return Trip in math", value=True)
    
    st.divider()
    app_mode = st.toggle("Show Absolute Best Choice", value=False)
    
    if st.button("Clear Map Selections", use_container_width=True):
        st.session_state.selected_servos = []
        st.rerun()

# ==========================================================
# RESULTS DISPLAY
# ==========================================================
if st.session_state.user_loc:
    u_lon, u_lat = st.session_state.user_loc
    
    if app_mode:
        res = get_matrix_results(u_lon, u_lat, stations_df, eff, litres, return_trip)
        if res is not None:
            winner = res.iloc[0]
            closest = res.sort_values("Dist (km)").iloc[0]
            
            st.success(f"Winner: {winner['Station']} is your best value overall.")
            
            if winner['Station'] != closest['Station']:
                extra_time = round(winner['Drive Time'] - closest['Drive Time'], 1)
                savings = round(closest['Total Trip Cost'] - winner['Total Trip Cost'], 2)
                st.warning(f"Trade-off: This is {extra_time} mins further away than your closest option ({closest['Station']}), but saves you ${savings} overall.")
            else:
                st.info("The absolute cheapest option is also the closest one to you.")

            res['Drive Time'] = res['Drive Time'].astype(str) + "m"
            st.dataframe(res, hide_index=True, use_container_width=True)
            
    elif len(st.session_state.selected_servos) == 2:
        picked_df = stations_df[stations_df['name'].isin(st.session_state.selected_servos)]
        res_df = get_matrix_results(u_lon, u_lat, picked_df, eff, litres, return_trip)
        
        if res_df is not None:
            winner = res_df.iloc[0]
            loser = res_df.iloc[1]
            savings = round(loser['Total Trip Cost'] - winner['Total Trip Cost'], 2)
            time_diff = round(abs(winner['Drive Time'] - loser['Drive Time']), 1)
            
            st.success(f"Winner: {winner['Station']} saves you ${savings}.")
            
            if winner['Drive Time'] > loser['Drive Time']:
                st.warning(f"Heads up: To save that ${savings}, you will have to drive an extra {time_diff} minutes.")
            elif winner['Drive Time'] < loser['Drive Time']:
                st.info(f"Bonus: It is also {time_diff} minutes faster to get there.")

            c1, c2 = st.columns(2)
            for i in range(len(res_df)):
                item = res_df.iloc[i]
                with [c1, c2][i]:
                    st.metric(item['Station'], f"${item['Total Trip Cost']}")
                    st.write(f"{item['Drive Time']} mins drive | {item['Dist (km)']}km")
