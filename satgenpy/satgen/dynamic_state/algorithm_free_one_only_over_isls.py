# The MIT License (MIT)
#
# Copyright (c) 2020 ETH Zurich
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import networkx as nx
import math


def algorithm_free_one_only_over_isls(
        output_dynamic_state_dir,
        time_since_epoch_ns,
        satellites,
        ground_stations,
        sat_net_graph_without_gs,
        ground_station_satellites_in_range,
        num_isls_per_sat,
        sat_neighbor_to_if,
        list_gsl_interfaces_info,
        prev_output,
        enable_verbose_logs
):
    """
    FREE-ONE ONLY OVER INTER-SATELLITE LINKS ALGORITHM

    "one"
    This algorithm assumes that every satellite and ground station has exactly 1 GSL interface.

    "free"
    This 1 interface is bound to a maximum outgoing bandwidth, but can send to any other
    GSL interface (well, satellite -> ground-station, and ground-station -> satellite) in
    range. ("free") There is no reciprocation of the bandwidth asserted.

    "only_over_isls"
    It calculates a forwarding state, which is essentially a single shortest path.
    It only considers paths which go over the inter-satellite network, and does not make use of ground
    stations relay. This means that every path looks like:
    (src gs) - (sat) - (sat) - ... - (sat) - (dst gs)

    As such, it should only be used to answer questions of:
    (a) reachability / RTT over time,
    (b) accurate bandwidth if only having a single communication pair.

    If this is not respected, it is e.g. possible that a ground station can only send at
    for example 10G, but all satellites in range can send to him in
    be used for questions of a single pair of communicating
    """

    if enable_verbose_logs:
        print("\nALGORITHM: FREE ONE ONLY OVER ISLS")

    # Check the graph
    if sat_net_graph_without_gs.number_of_nodes() != len(satellites):
        raise ValueError("Number of nodes in the graph does not match the number of satellites")
    for sid in range(len(satellites)):
        for n in sat_net_graph_without_gs.neighbors(sid):
            if n >= len(satellites):
                raise ValueError("Graph cannot contain satellite-to-ground-station links")

    #################################
    # BANDWIDTH STATE
    #

    # There is only one GSL interface for each node (pre-condition), which as-such will get the entire bandwidth
    output_filename = output_dynamic_state_dir + "/gsl_if_bandwidth_" + str(time_since_epoch_ns) + ".txt"
    if enable_verbose_logs:
        print("  > Writing interface bandwidth state to: " + output_filename)
    with open(output_filename, "w+") as f_out:
        if time_since_epoch_ns == 0:
            for node_id in range(len(satellites)):
                f_out.write("%d,%d,%f\n"
                            % (node_id, num_isls_per_sat[node_id],
                               list_gsl_interfaces_info[node_id]["aggregate_max_bandwidth"]))
            for node_id in range(len(satellites), len(satellites) + len(ground_stations)):
                f_out.write("%d,%d,%f\n"
                            % (node_id, 0, list_gsl_interfaces_info[node_id]["aggregate_max_bandwidth"]))

    #################################
    # FORWARDING STATE
    #

    # Calculate shortest paths
    if enable_verbose_logs:
        print("  > Calculating Floyd-Warshall for graph without ground-station relays")
    # (Note: Numpy has a deprecation warning here because of how networkx uses matrices)
    dist_sat_net_without_gs = nx.floyd_warshall_numpy(sat_net_graph_without_gs)

    # Forwarding state
    fstate = {}
    prev_fstate = None
    if prev_output is not None:
        prev_fstate = prev_output["fstate"]

    # Now write state to file for complete graph
    output_filename = output_dynamic_state_dir + "/fstate_" + str(time_since_epoch_ns) + ".txt"
    if enable_verbose_logs:
        print("  > Writing forwarding state to: " + output_filename)
    with open(output_filename, "w+") as f_out:

        # Satellites to ground stations
        # From the satellites attached to the destination ground station,
        # select the one which promises the shortest path to the destination ground station (getting there + last hop)
        dist_satellite_to_ground_station = {}
        for curr in range(len(satellites)):
            for dst_gs in range(len(ground_stations)):
                dst_gs_node_id = len(satellites) + dst_gs

                # The satellite this ground station is match
                possible_dst_sats = ground_station_satellites_in_range[dst_gs]
                possibilities = []
                for b in possible_dst_sats:
                    if not math.isinf(dist_sat_net_without_gs[(curr, b[1])]):  # Must be reachable
                        possibilities.append(
                            (
                                dist_sat_net_without_gs[(curr, b[1])] + b[0],
                                b[1]
                            )
                        )
                possibilities = list(sorted(possibilities))

                # By default, if there is no satellite in range for the
                # destination ground station, it will be dropped (indicated by -1)
                next_hop_decision = (-1, -1, -1)
                distance_to_ground_station_m = float("inf")
                if len(possibilities) > 0:
                    dst_sat = possibilities[0][1]
                    distance_to_ground_station_m = possibilities[0][0]

                    # If the current node is not that satellite, determine how to get to the satellite
                    if curr != dst_sat:

                        # Among its neighbors, find the one which promises the
                        # lowest distance to reach the destination satellite
                        best_dist = 1000000000000000
                        for n in sat_net_graph_without_gs.neighbors(curr):
                            if dist_sat_net_without_gs[(curr, n)] + dist_sat_net_without_gs[(n, dst_sat)] < best_dist:
                                next_hop_decision = (n, sat_neighbor_to_if[(curr, n)], sat_neighbor_to_if[(n, curr)])
                                best_dist = dist_sat_net_without_gs[(curr, n)] + dist_sat_net_without_gs[(n, dst_sat)]

                    else:
                        # This is the destination satellite, as such the next hop is the ground station itself
                        next_hop_decision = (dst_gs_node_id, num_isls_per_sat[dst_sat], 0)

                # Write to forwarding state
                dist_satellite_to_ground_station[(curr, dst_gs_node_id)] = distance_to_ground_station_m
                if not prev_fstate or prev_fstate[(curr, dst_gs_node_id)] != next_hop_decision:
                    f_out.write("%d,%d,%d,%d,%d\n"
                                % (curr, dst_gs_node_id,
                                   next_hop_decision[0], next_hop_decision[1], next_hop_decision[2]))
                fstate[(curr, dst_gs_node_id)] = next_hop_decision

        # Ground stations to ground stations
        # Choose the source satellite which promises the shortest path
        for src_gid in range(len(ground_stations)):
            for dst_gid in range(len(ground_stations)):
                if src_gid != dst_gid:
                    src_gs_node_id = len(satellites) + src_gid
                    dst_gs_node_id = len(satellites) + dst_gid

                    possible_src_sats = ground_station_satellites_in_range[src_gid]
                    possibilities = []
                    for a in possible_src_sats:
                        best_distance_offered_m = dist_satellite_to_ground_station[(a[1], dst_gs_node_id)]
                        if not math.isinf(best_distance_offered_m):
                            possibilities.append(
                                (
                                    a[0] + best_distance_offered_m,
                                    a[1]
                                )
                            )
                    possibilities = sorted(possibilities)

                    # By default, if there is no satellite in range for one of the
                    # ground stations, it will be dropped (indicated by -1)
                    next_hop_decision = (-1, -1, -1)
                    if len(possibilities) > 0:
                        src_sat_id = possibilities[0][1]
                        next_hop_decision = (src_sat_id, 0, num_isls_per_sat[src_sat_id])

                    # Update forwarding state
                    if not prev_fstate or prev_fstate[(src_gs_node_id, dst_gs_node_id)] != next_hop_decision:
                        f_out.write("%d,%d,%d,%d,%d\n"
                                    % (src_gs_node_id, dst_gs_node_id,
                                       next_hop_decision[0], next_hop_decision[1], next_hop_decision[2]))
                    fstate[(src_gs_node_id, dst_gs_node_id)] = next_hop_decision

    if enable_verbose_logs:
        print("")

    return {
        "fstate": fstate
    }
