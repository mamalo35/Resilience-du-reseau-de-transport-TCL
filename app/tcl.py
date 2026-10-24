import pandas as pd
import networkx as nx
import math
from collections import defaultdict
import heapq

WALKING_SPEED_KMH     = 4.0
WALKING_SPEED_MS      = WALKING_SPEED_KMH * 1000 / 3600
WALKING_DETOUR_FACTOR = 1.3  
TRANSFER_WALK_SECONDS = 90
METRO_ROUTE_IDS       = {"A", "B", "C", "D"}   
TRAM_ROUTE_TYPE       = 0 
REFERENCE_MONDAY = "2026-06-01"                

def haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(math.radians(lon2-lon1)/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def walking_time_seconds(dist_m):
    """Temps de marche en secondes : distance à vol d'oiseau x coefficient de détour."""
    return (dist_m * WALKING_DETOUR_FACTOR) / WALKING_SPEED_MS

def format_duration(s):
    s = int(s); m, sec = divmod(s, 60)
    return f"{sec} sec" if m == 0 else f"{m} min {sec} sec"

def to_seconds(t):
    """HH:MM:SS -> secondes (supporte les heures GTFS > 24)."""
    h, m, s = map(int, str(t).split(":"))
    return h*3600 + m*60 + s

def seconds_to_hhmm(s):
    s = int(s); h, r = divmod(s, 3600)
    return f"{h:02d}:{r//60:02d}"

def get_active_services(calendar_df, calendar_dates_df, date_str):
    """Retourne les service_id actifs pour une date donnée (calendrier + exceptions GTFS)."""
    date_ts = pd.Timestamp(date_str)
    day_col = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][date_ts.weekday()]
    active  = set(calendar_df[
        (calendar_df[day_col] == 1) &
        (calendar_df["start_date"] <= date_ts) &
        (calendar_df["end_date"]   >= date_ts)
    ]["service_id"])
    exc     = calendar_dates_df[calendar_dates_df["date"] == date_ts]
    active -= set(exc[exc["exception_type"] == 2]["service_id"])
    active |= set(exc[exc["exception_type"] == 1]["service_id"])
    return active


def build_avg_wait(routes, stop_times_df, trips_df, calendar_df, calendar_dates_df,
                   reference_monday = "2026-06-01"):
    """
    Calcule le temps d'attente moyen en secondes par (ligne, heure)
    à partir du lundi de référence.

    Retourne un dict : (route_id, heure_int) -> attente_secondes
    heure_int est un entier 0..23 (l'heure à laquelle on arrive en station).

    Les trips GTFS incluent les deux directions (terminus A -> terminus B et retour).
    On divise donc le nombre de passages par 2 pour obtenir la fréquence par direction.
    """
    # Services actifs le lundi de référence
    active_svc = get_active_services(calendar_df, calendar_dates_df, reference_monday)

    all_route_ids = route_ids(routes)

    # Trips métro actifs ce jour
    ref_trips = trips_df[
        trips_df["route_id"].isin(all_route_ids) &
        trips_df["service_id"].isin(active_svc)
    ][["trip_id", "route_id"]]

    # On prend uniquement le premier arrêt de chaque trip (stop_sequence == 1)
    # = l'heure de départ du train depuis son terminus
    first_stops = stop_times_df[
        stop_times_df["stop_sequence"] == 1
    ][["trip_id", "departure_time"]].copy()

    first_stops = first_stops.merge(ref_trips, on="trip_id", how="inner")

    # Extraction de l'heure (modulo 24 pour les heures GTFS > 23)
    first_stops["heure"] = first_stops["departure_time"].apply(
        lambda t: int(str(t).split(":")[0]) % 24
    )

    # Comptage des passages par (ligne, heure)
    counts = (
        first_stops
        .groupby(["route_id", "heure"])
        .size()
        .reset_index(name="nb_passages")
    )

    # Division par 2 : chaque ligne a 2 directions, on veut la fréquence par sens
    # Intervalle entre deux trains = 3600 / (nb_passages / 2)
    # Attente moyenne = intervalle / 2  (on arrive en moyenne à mi-intervalle)
    # Si aucun passage dans un créneau -> on met 1800 s (prudence)
    avg_wait = {}
    for _, row in counts.iterrows():
        nb_par_sens = row["nb_passages"] / 2
        intervalle  = 3600 / nb_par_sens
        avg_wait[(row["route_id"], int(row["heure"]))] = intervalle / 2

    return avg_wait


def get_wait(avg_wait, route_id, clock_seconds, fallback_s=1800):
    """
    Retourne le temps d'attente moyen (en secondes) pour une ligne
    à l'heure correspondant à clock_seconds.
    """
    heure = (int(clock_seconds) // 3600) % 24
    return avg_wait.get((route_id, heure), fallback_s)

def route_ids(routes):
    tram_route_ids = set(routes[routes["route_type"] == TRAM_ROUTE_TYPE]["route_id"])
    all_route_ids  = METRO_ROUTE_IDS | tram_route_ids
    return all_route_ids

def construire_graphe(routes, stops, trips, stop_times, calendar, calendar_dates, date_cible):
    # Services actifs pour date_cible
    active_services = get_active_services(calendar, calendar_dates, date_cible)
    all_route_ids  = route_ids(routes)

    # Trips métro + tram actifs ce jour
    network_trips        = trips[trips["route_id"].isin(all_route_ids)]
    active_network_trips = network_trips[network_trips["service_id"].isin(active_services)]

    # Stop times filtrés
    metro_stop_times = stop_times[stop_times["trip_id"].isin(active_network_trips["trip_id"])].copy()
    metro_stop_times = metro_stop_times.sort_values(["trip_id", "stop_sequence"])
    metro_stop_times = metro_stop_times.merge(trips[["trip_id", "route_id"]], on="trip_id", how="inner")
    metro_stops      = stops[stops["stop_id"].isin(metro_stop_times["stop_id"])].drop_duplicates(subset="stop_id").copy()

    
    # Graphe brut par stop_id
    G = nx.Graph()
    for _, s in metro_stops.iterrows():
        G.add_node(s["stop_id"], name=s["stop_name"], lat=float(s["stop_lat"]), lon=float(s["stop_lon"]))
    for trip_id, grp in metro_stop_times.sort_values(["trip_id","stop_sequence"]).groupby("trip_id"):
        sids, route = grp["stop_id"].tolist(), grp["route_id"].iloc[0]
        for i in range(len(sids)-1):
            G.add_edge(sids[i], sids[i+1], route=route)

    # Fusion par nom de station
    name_to_node = {}
    G2 = nx.Graph()
    for node, data in G.nodes(data=True):
        if data["name"] not in name_to_node:
            name_to_node[data["name"]] = node
            G2.add_node(node, **data)
    for u, v, data in G.edges(data=True):
        u2, v2 = name_to_node[G.nodes[u]["name"]], name_to_node[G.nodes[v]["name"]]
        if u2 != v2:
            G2.add_edge(u2, v2, **data)

    G2_weighted = build_weighted_graph(G2, metro_stop_times, metro_stops)

    return G2_weighted

def build_weighted_graph(G2_in, mst, stops_df):
    """Graphe pondéré : poids = temps médian de parcours entre deux stations voisines."""
    col      = "departure_time" if "departure_time" in mst.columns else "arrival_time"
    sid2name = stops_df.set_index("stop_id")["stop_name"].to_dict()
    edge_data = defaultdict(lambda: defaultdict(list))

    for _, grp in mst.sort_values(["trip_id","stop_sequence"]).groupby("trip_id"):
        sids, times, route = grp["stop_id"].tolist(), grp[col].tolist(), grp["route_id"].iloc[0]
        for i in range(len(sids)-1):
            n1, n2 = sid2name.get(str(sids[i])), sid2name.get(str(sids[i+1]))
            if not n1 or not n2 or n1 == n2: continue
            t1, t2 = to_seconds(times[i]), to_seconds(times[i+1])
            if t2 > t1: edge_data[(n1,n2)][route].append(t2-t1)

    n2n = {data["name"]: node for node, data in G2_in.nodes(data=True)}
    Gw  = nx.Graph()
    for node, data in G2_in.nodes(data=True): Gw.add_node(node, **data)

    for (n1,n2), rd in edge_data.items():
        if n1 not in n2n or n2 not in n2n: continue
        u, v  = n2n[n1], n2n[n2]
        durs  = [d for ds in rd.values() for d in ds]
        med_t = sorted(durs)[len(durs)//2]
        rset  = set(rd.keys())
        if Gw.has_edge(u,v):
            Gw[u][v]["weight"] = min(Gw[u][v]["weight"], med_t)
            Gw[u][v]["routes"] |= rset
        else:
            Gw.add_edge(u, v, weight=med_t, routes=rset)
    return Gw


def nearest_station(lat, lon, graph):
    best, dist = None, float("inf")
    for node, data in graph.nodes(data=True):
        d = haversine(lat, lon, data["lat"], data["lon"])
        if d < dist: dist, best = d, node
    return best, dist


def nearest_stations(lat, lon, graph, n=5):
    """Retourne les n stations les plus proches avec leurs distances."""
    dists = [(haversine(lat, lon, data["lat"], data["lon"]), node)
             for node, data in graph.nodes(data=True)]
    return sorted(dists)[:n]


def dijkstra_with_wait(graph, source, target, clock_start, avg_wait):
    """
    Dijkstra sur états (node, route).

    Un état = (station, ligne_courante). On ne finalise un état qu'une seule fois,
    mais un même nœud physique peut être visité via plusieurs lignes différentes.
    Cela garantit que le chemin optimal global est toujours trouvé,
    même quand une ligne moins fréquente offre un trajet plus court
    grâce à moins de correspondances.

    Les cycles sont évités par best_cost : on ne remet un état dans le heap
    que si on a trouvé un coût strictement inférieur.
    """

    counter   = 0
    heap      = [(0, counter, source, None)]
    visited   = set()          # états (node, route) déjà finalisés
    best_cost = {}             # (node, route) -> meilleur coût vu
    parent    = {}             # (node, route) -> (node_parent, route_parent)

    while heap:
        cost, _, node, cur_route = heapq.heappop(heap)

        state = (node, cur_route)
        if state in visited:
            continue
        visited.add(state)

        if node == target:
            path  = []
            s     = state
            while s in parent:
                path.append(s)
                s = parent[s]
            path.append((source, None))
            path.reverse()
            return cost, path

        clock_here = clock_start + cost

        for neighbor in graph.neighbors(node):
            edge_routes = graph[node][neighbor].get("routes", set())
            edge_weight = graph[node][neighbor].get("weight", float("inf"))

            for route in edge_routes:
                new_state = (neighbor, route)
                if new_state in visited:
                    continue

                if cur_route is None:
                    # Premier embarquement
                    wait       = get_wait(avg_wait, route, clock_here)
                    transition = wait + edge_weight
                elif route == cur_route:
                    # Même ligne : pas d'attente
                    transition = edge_weight
                else:
                    # Changement de ligne : marche + attente de la nouvelle ligne
                    wait       = get_wait(avg_wait, route, clock_here)
                    transition = TRANSFER_WALK_SECONDS + wait + edge_weight

                new_cost = cost + transition

                if new_cost < best_cost.get(new_state, float("inf")):
                    best_cost[new_state] = new_cost
                    parent[new_state]    = (node, cur_route)
                    counter += 1
                    heapq.heappush(heap, (new_cost, counter, neighbor, route))

    return float("inf"), []


def compute_total_travel_time(date, lat_a, lon_a, lat_b, lon_b,
                               departure_hhmm, graph, avg_wait, n_candidates=10):
    """
    Calcule le trajet complet A -> B avec Dijkstra sur états (station, ligne).
    Teste les n_candidates stations les plus proches au départ ET à l'arrivée
    pour trouver le trajet total le plus court — crucial quand une ligne est fermée
    et que la station la plus proche n'est plus accessible.
    """

    date_ts  = pd.Timestamp(date)
    day_name = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][date_ts.weekday()]

    h, m     = map(int, departure_hhmm.split(":"))
    depart_s = h*3600 + m*60

    dep_candidates = nearest_stations(lat_a, lon_a, graph, n=n_candidates)
    arr_candidates = nearest_stations(lat_b, lon_b, graph, n=n_candidates)

    best_result = None

    for dd, nd in dep_candidates:
        walk_dep_s = walking_time_seconds(dd)
        clock      = depart_s + walk_dep_s

        for da, na in arr_candidates:
            walk_arr_s = walking_time_seconds(da)

            if nd == na:
                total_s = walk_dep_s + walk_arr_s
                result  = {
                    "date": date, "jour": day_name, "depart_hhmm": departure_hhmm,
                    "station_depart":        graph.nodes[nd]["name"],
                    "station_arrivee":       graph.nodes[na]["name"],
                    "dist_marche_dep_m":     round(dd, 1),
                    "temps_marche_dep_s":    round(walk_dep_s),
                    "heure_arrivee_station": seconds_to_hhmm(clock),
                    "ligne_depart": None, "temps_attente_dep_s": 0,
                    "chemin_metro": [graph.nodes[nd]["name"]], "nb_stations": 0, "temps_metro_s": 0,
                    "correspondances": [],
                    "dist_marche_arr_m":  round(da, 1),
                    "temps_marche_arr_s": round(walk_arr_s),
                    "temps_total_s":      round(total_s),
                    "heure_arrivee_B":    seconds_to_hhmm(depart_s + total_s),
                    "mode": "tc",
                }
            else:
                ride_cost, state_path = dijkstra_with_wait(graph, nd, na, clock, avg_wait)

                if ride_cost == float("inf"):
                    continue

                node_path   = [s[0] for s in state_path]
                route_path  = [s[1] for s in state_path]
                first_route = route_path[1] if len(route_path) > 1 else None
                wait_dep_s  = get_wait(avg_wait, first_route, clock) if first_route else 0

                tdetail  = []
                run_cost = 0
                clock2   = clock + wait_dep_s
                for i in range(1, len(state_path)):
                    prev_node, prev_route = state_path[i-1]
                    cur_node,  cur_route  = state_path[i]
                    edge_w = graph[prev_node][cur_node].get("weight", 0)
                    if prev_route is not None and cur_route != prev_route:
                        arr_tr  = clock2 + run_cost
                        wait_tr = get_wait(avg_wait, cur_route, arr_tr + TRANSFER_WALK_SECONDS)
                        tdetail.append({
                            "station":          graph.nodes[prev_node]["name"],
                            "ligne_quittee":    prev_route,
                            "ligne_prise":      cur_route,
                            "heure_arrivee":    seconds_to_hhmm(arr_tr),
                            "marche_station_s": TRANSFER_WALK_SECONDS,
                            "attente_s":        round(wait_tr),
                        })
                        run_cost += TRANSFER_WALK_SECONDS + wait_tr
                    run_cost += edge_w

                total_s = walk_dep_s + ride_cost + walk_arr_s
                result  = {
                    "date":                  date,
                    "jour":                  day_name,
                    "depart_hhmm":           departure_hhmm,
                    "station_depart":        graph.nodes[nd]["name"],
                    "station_arrivee":       graph.nodes[na]["name"],
                    "dist_marche_dep_m":     round(dd, 1),
                    "temps_marche_dep_s":    round(walk_dep_s),
                    "heure_arrivee_station": seconds_to_hhmm(clock),
                    "ligne_depart":          first_route,
                    "temps_attente_dep_s":   round(wait_dep_s),
                    "chemin_metro":          [graph.nodes[n]["name"] for n in node_path],
                    "nb_stations":           len(node_path) - 1,
                    "temps_metro_s":         round(ride_cost),
                    "correspondances":       tdetail,
                    "dist_marche_arr_m":     round(da, 1),
                    "temps_marche_arr_s":    round(walk_arr_s),
                    "temps_total_s":         round(total_s),
                    "heure_arrivee_B":       seconds_to_hhmm(depart_s + total_s),
                    "mode":                  "tc",
                }

            if best_result is None or result["temps_total_s"] < best_result["temps_total_s"]:
                best_result = result

    # Fallback : aucun chemin TC trouvé → marche directe A→B
    if best_result is None:
        dist_directe = haversine(lat_a, lon_a, lat_b, lon_b)
        walk_total   = walking_time_seconds(dist_directe * WALKING_DETOUR_FACTOR)
        return {
            "date": date, "jour": day_name, "depart_hhmm": departure_hhmm,
            "station_depart": "—", "station_arrivee": "—",
            "dist_marche_dep_m": 0, "temps_marche_dep_s": 0,
            "heure_arrivee_station": departure_hhmm,
            "ligne_depart": None, "temps_attente_dep_s": 0,
            "chemin_metro": [], "nb_stations": 0, "temps_metro_s": 0,
            "correspondances": [],
            "dist_marche_arr_m": 0, "temps_marche_arr_s": round(walk_total),
            "temps_total_s": round(walk_total),
            "heure_arrivee_B": seconds_to_hhmm(depart_s + walk_total),
            "mode": "marche",
        }

    return best_result


def print_trajet(r):
    if "error" in r:
        print("Erreur :", r["error"]); return
    sep = "=" * 60
    print(sep)
    print("  ITINERAIRE A -> B")
    print(f"  Date : {r['date']} ({r['jour']})")
    print(sep)
    print(f"  Depart            : {r['depart_hhmm']}")

    if r.get("mode") == "marche":
        print(f"  ⚠ Aucun TC disponible — trajet entièrement à pied")
        print(f"  Marche directe    : {format_duration(r['temps_total_s'])}")
        print(f"  TOTAL             : {format_duration(r['temps_total_s'])}")
        print(f"  Arrivee estimee   : {r['heure_arrivee_B']}")
        print(sep)
        return

    print(f"  Marche vers       : {r['station_depart']} ({r['dist_marche_dep_m']} m, {format_duration(r['temps_marche_dep_s'])})")
    print(f"  Arrivee station   : {r['heure_arrivee_station']}")
    print(f"  Ligne prise       : {r['ligne_depart']}")
    print(f"  Attente moyenne   : {format_duration(r['temps_attente_dep_s'])}")
    print()
    print(f"  Trajet           : {' -> '.join(r['chemin_metro'])}")
    print(f"  Nb arrets        : {r['nb_stations']}")
    for i, c in enumerate(r["correspondances"], 1):
        print(f"  Correspondance {i} : {c['station']} (ligne {c['ligne_quittee']} -> {c['ligne_prise']})")
        print(f"    Arrivee: {c['heure_arrivee']} | Marche: {format_duration(c['marche_station_s'])} | Attente ligne {c['ligne_prise']}: {format_duration(c['attente_s'])}")
    print()
    print(f"  Marche finale    : {r['dist_marche_arr_m']} m ({format_duration(r['temps_marche_arr_s'])})")
    print(f"  TOTAL            : {format_duration(r['temps_total_s'])}")
    print(f"  Arrivee estimee  : {r['heure_arrivee_B']}")
    print(sep)



def fermer_ligne(graph, ligne):
    """
    Retourne une copie du graphe sans la ligne donnée.
    - Si une arête n'a que cette ligne : elle est supprimée
    - Si une arête a plusieurs lignes : on retire juste cette ligne
    - Les nœuds qui n'ont plus aucune arête sont supprimés
    """
    G_ferme = graph.copy()
    edges_to_remove = []

    for u, v, d in G_ferme.edges(data=True):
        routes = d.get("routes", set()) - {ligne}
        if not routes:
            edges_to_remove.append((u, v))
        else:
            G_ferme[u][v]["routes"] = routes

    G_ferme.remove_edges_from(edges_to_remove)

    # Supprimer les nœuds isolés (plus aucune arête = inaccessibles)
    noeuds_isoles = [n for n, deg in G_ferme.degree() if deg == 0]
    G_ferme.remove_nodes_from(noeuds_isoles)
    return G_ferme

def stat_lat_lon(stops, name):
    """
    Prend en argument un nom de station et retourne sa latitude et longitude.
    Pour les stations homonyme on prend la 1ère dans la liste car elle sont très proche l'une de l'autre.
    Amélioration possible, parcourir le graphe plutot que les stops
    """

    lignes_station = stops[stops["stop_name"] == name]
    if lignes_station.empty:
        return None
    lat = lignes_station["stop_lat"].iloc[0]
    lon = lignes_station["stop_lon"].iloc[0]
    return lat,lon
