import streamlit as st
import pandas as pd
import tcl
import datetime
import networkx as nx
import plotly.express as px

REFERENCE_MONDAY = "2026-06-01" 

st.title("Réseau TCL")

@st.cache_data
def charger_gtfs():
    # 1. Lecture des fichiers
    stops          = pd.read_csv("data/stops.txt")
    trips          = pd.read_csv("data/trips.txt")
    stop_times     = pd.read_csv("data/stop_times.txt")
    routes         = pd.read_csv("data/routes.txt")
    calendar       = pd.read_csv("data/calendar.txt")
    calendar_dates = pd.read_csv("data/calendar_dates.txt")

    # 2. Conversion des dates (texte 20260601 -> vraie date)
    calendar["start_date"] = pd.to_datetime(calendar["start_date"].astype(str), format="%Y%m%d")
    calendar["end_date"]   = pd.to_datetime(calendar["end_date"].astype(str), format="%Y%m%d")
    calendar_dates["date"] = pd.to_datetime(calendar_dates["date"].astype(str), format="%Y%m%d")

    # 3. Les identifiants d'arrêts en texte, pour pouvoir les comparer
    stops["stop_id"]      = stops["stop_id"].astype(str)
    stop_times["stop_id"] = stop_times["stop_id"].astype(str)

    # 4. On renvoie tout dans un dictionnaire
    return {
        "stops": stops,
        "trips": trips,
        "stop_times": stop_times,
        "routes": routes,
        "calendar": calendar,
        "calendar_dates": calendar_dates,
    }

@st.cache_data
def calculer_attentes(routes, stop_times_df, trips_df, calendar_df, calendar_dates_df,reference_monday):
    tmps = tcl.build_avg_wait(routes, stop_times_df, trips_df, calendar_df, calendar_dates_df,
                   reference_monday)
    return tmps

@st.cache_resource
def obtenir_graphe(routes, stops, trips, stop_times, calendar, calendar_dates, date_cible):
    graphe = tcl.construire_graphe(routes, stops, trips, stop_times, calendar, calendar_dates, date_cible)
    return graphe



_gtfs = charger_gtfs()


with st.sidebar:
    date = st.date_input("Date du trajet", value=datetime.date(2026, 6, 2), min_value=datetime.date(2026, 4, 1),
        max_value=datetime.date(2026, 8, 31))

G = obtenir_graphe(_gtfs["routes"], _gtfs["stops"], _gtfs["trips"], _gtfs["stop_times"], _gtfs["calendar"], _gtfs["calendar_dates"], str(date))
station = sorted(nx.get_node_attributes(G, "name").values())

with st.sidebar:
    heure_depart = st.time_input("Heure de départ du trajet", value = datetime.time(8,15))
    station_depart = st.selectbox("Station de départ", station)
    station_fin = st.selectbox("Station d'arrivée", station)
    calcule = st.button("Calculer", type="primary")


st.write("Date de départ : ", date)
st.write("Heure de départ", heure_depart.strftime("%H:%M"))

if calcule :
    avg_wait = calculer_attentes(_gtfs["routes"],_gtfs["stop_times"], _gtfs["trips"], _gtfs["calendar"],
                              _gtfs["calendar_dates"], reference_monday = REFERENCE_MONDAY)
    lat_a, lon_a = tcl.stat_lat_lon(_gtfs["stops"], station_depart)
    lat_b, lon_b = tcl.stat_lat_lon(_gtfs["stops"], station_fin)
    resultat = tcl.compute_total_travel_time(str(date), lat_a, lon_a, lat_b, lon_b,
                                            heure_depart.strftime("%H:%M"), G, avg_wait, n_candidates=10)
    st.write("Heure d'arrivée estimée", resultat["heure_arrivee_B"])
    st.write("Durée totale", round(resultat["temps_total_s"]/60, 2))
    st.write("Nombre de correspondance", len(resultat["correspondances"]))

    lat, lon, col = [], [], []
    ligne_dep = resultat["ligne_depart"]
    j = 0
    for i in range(0,len(resultat["chemin_metro"])):
        la, lo = tcl.stat_lat_lon(_gtfs["stops"], resultat["chemin_metro"][i])
        lat.append(la)
        lon.append(lo)
        col.append(ligne_dep)
        if len(resultat["correspondances"]) > j and resultat["chemin_metro"][i] == resultat["correspondances"][j]["station"]:
            ligne_dep = resultat["correspondances"][j]["ligne_prise"]
            j = j+1
            lat.append(la)
            lon.append(lo)
            col.append(ligne_dep)

    lat_lon = pd.DataFrame({"latitude": lat , "longitude": lon, "couleur": col})

    fig = px.line_map(lat_lon, lat = "latitude", lon="longitude", color = "couleur")
    fig.update_layout(map_style="carto-voyager")
    st.plotly_chart(fig)

