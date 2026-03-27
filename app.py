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

# ==========================================================
# SESSION STATE (The "Memory" of the app)
# ==========================================================
if 'center' not in st.session_state: st.session_state.center = {"lat": -34.9285, "lng": 138.6007}
if 'zoom' not in st.session_state: st.session_state.zoom = 12
if 'selected_servos' not in st.session_state: st.session_state.selected_servos = []
if 'user_loc' not in st.session_state: st.session_state.user_loc = None

# ==========================================================
# CORE LOGIC
# ==========================================================
def load_stations():
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
    # This remains the same, calculating total cost based on distance
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
    except: return None

stations_df = load_stations()
st.title("SmartFuel Finder")

# Top Search & Location
col1, col2 = st.columns([4, 1])
with col1:
    manual_address = st.text_input("Search", placeholder="Suburb...", label_visibility="collapsed")
with col2:
    loc = streamlit_geolocation()

if manual_address:
    # (Geocoding logic would go here)
    pass
elif loc and loc.get('latitude'):
    st.session_state.user_loc = [loc['longitude'], loc['latitude']]
    st.session_state.center = {"lat": loc['latitude'], "lng": loc['longitude']}

# Fuel Selection
f_col1, f_col2 = st.columns([1, 1])
with f_col2:
    fuel_choice = st.selectbox("Fuel", options=["U91", "U95", "U98", "Diesel"], label_visibility="collapsed")
stations_df['current_price'] = stations_df[f'price_{fuel_choice}']

# ==========================================================
# THE MAP (Sticky Mode)
# ==========================================================
m = folium.Map(location=[st.session_state.center["lat"], st.session_state.center["lng"]], 
               zoom_start=st.session_state.zoom)

for _, row in stations_df.iterrows():
    is_sel = row['name'] in st.session_state.selected_servos
    color = "black" if is_sel else ("#28a745" if row['current_price'] < 1.90 else "#ffc107")
    
    folium.Marker(
        [row['lat'], row['lon']],
        icon=folium.DivIcon(html=f"""<div style="color:white; background:{color}; padding:5px; border-radius:4px; 
            border:1px solid black; width:50px; text-align:center; font-weight:bold;">${row['current_price']:.2f}</div>"""),
        popup=row['name']
    ).add_to(m)

# st_folium with state preservation
st_data = st_folium(
    m, 
    center=st.session_state.center,
    zoom=st.session_state.zoom,
    use_container_width=True, 
    height=400, 
    key="fuel_map"
)

# Handle Map Updates (Zoom/Pan) without resetting
if st_data and st_data.get("center"):
    st.session_state.center = st_data["center"]
    st.session_state.zoom = st_data["zoom"]

# Handle Clicks
if st_data and st_data.get('last_object_clicked_popup'):
    clicked = st_data['last_object_clicked_popup']
    if clicked != "Start Location":
        if clicked not in st.session_state.selected_servos:
            st.session_state.selected_servos.append(clicked)
            if len(st.session_state.selected_servos) > 2:
                st.session_state.selected_servos.pop(0)
            st.rerun()

# ==========================================================
# DYNAMIC RESULTS (Single or Double)
# ==========================================================
st.divider()

if st.session_state.user_loc and st.session_state.selected_servos:
    u_lon, u_lat = st.session_state.user_loc
    picked_df = stations_df[stations_df['name'].isin(st.session_state.selected_servos)]
    
    # We always need vehicle settings for the math
    with st.expander("⚙️ Adjust Car Settings", expanded=False):
        v_type = st.selectbox("Vehicle Type", options=list(VEHICLE_TYPES.keys()))
        eff = VEHICLE_TYPES[v_type]
        litres = st.slider("Refuel Amount (L)", 10, 100, 50)
        return_trip = st.toggle("Include Return Trip", value=True)

    res_df = get_matrix_results(u_lon, u_lat, picked_df, eff, litres, return_trip)
    
    if res_df is not None:
        if len(st.session_state.selected_servos) == 1:
            # Layout for SINGLE SERVO click
            item = res_df.iloc[0]
            st.subheader(f"📍 {item['Station']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Cost", f"${item['Total Trip Cost']}")
            c2.metric("Drive Time", f"{item['Drive Time']}m")
            c3.metric("Distance", f"{item['Dist (km)']}km")
            st.info("Click another servo on the map to compare them 1v1.")
        
        else:
            # Layout for 1v1 COMPARISON
            winner = res_df.iloc[0]
            loser = res_df.iloc[1]
            savings = round(loser['Total Trip Cost'] - winner['Total Trip Cost'], 2)
            
            st.success(f"🏆 {winner['Station']} is better value by ${savings}!")
            col_a, col_b = st.columns(2)
            for i, row in res_df.iterrows():
                with [col_a, col_b][i]:
                    st.metric(row['Station'], f"${row['Total Trip Cost']}")
                    st.caption(f"{row['Drive Time']} mins | {row['Dist (km)']}km")

if st.button("Clear Selections", use_container_width=True):
    st.session_state.selected_servos = []
    st.rerun()
