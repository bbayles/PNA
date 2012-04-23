#!/usr/bin/env python
#
# Copyright 2011 Washington University in St Louis
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Main workhorse for handling internal data representations
# all_data is formatted as follows (generated by PNALogParser)
# [ { end-time:int, size:int,
#     sessions:[ { local-ip:int,
#                  remote-ip:int,
#                  local-port:short,
#                  remote-port:short,
#                  packets-out:int,
#                  packets-in:int,
#                  bytes-out:int,
#                  bytes-in:int,
#                  begin-time:int,
#                  protocol:byte,
#                  first-direction:byte }, ... ] }, ... ]

import re
from datetime import datetime, date as dt_date, time as dt_time, timedelta
import time
from parse import PNALogParser

__version__ = 'model_0.1.0-py'

class PNADefaults :
    sort_key = 'sessions'
    threshold = 0
    filters = {}

class PNAModel :
    ip_fmt = '123.45.67.89/32'
    ip_re = re.compile('^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(/\d{1,2})?$')

    port_fmt = '65535'
    # three formats: 65535; 65535-65535 inclusive; 65535,80,22
    port_re = re.compile('^(\d{1,5})\-?(\d{1,5})?(\,\d{1,5})*$')

    time_fmt = 'YYYYMMDDHHMMSS'
    time_re = re.compile('^(\d{4})(\d{1,2})?(\d{1,2})?(\d{1,2})?(\d{1,2})?(\d{1,2})?$')

    latest_fmt = 'nnnn'
    latest_re = re.compile('^(\d+)$')

    sort_keys = ('raw', 'connections', 'sessions',
                 'tcp-ports', 'tcp-packets', 'tcp-octets',
                 'udp-ports', 'udp-packets', 'udp-octets',
                 'all-ports', 'all-packets', 'all-octets',)
    filters    = ('local-ip','remote-ip',
                  'source-port','destination-port',
                  'begin-time','end-time','latest',)
    filter_res = (ip_re, ip_re, port_re, port_re, time_re, time_re, latest_re,)

    # set up initial structures
    def __init__(self, watch_dir=None) :
        self.settings = {'sort-key':PNADefaults.sort_key,
                         'threshold':PNADefaults.threshold,
                         'filters':PNADefaults.filters,}
        self.parser = PNALogParser()
        self.all_data = [ ]
        self.cache = {'key':None,'threshold':None,'filters':None,'valid':False}

    def filter_reject(self, f_name, value) :
        # make sure this filter exists
        if f_name not in self.settings['filters'] :
            return False
        f_value = self.settings['filters'][f_name]

        if f_name == 'local-ip' or f_name == 'remote-ip' :
            # see if we should only match a netmask
            f_value = f_value.split('/')
            if len(f_value) == 1 :
                # no netmask, match full IP
                if self.int2ip(value) == f_value[0] :
                    return False
                else :
                    return True
            else :
                # netmask, check IP against subnet IP
                filter_ip = self.ip2int(f_value[0])
                filter_bits = int(f_value[1])
                mask = 0
                for i in range(32) :
                    mask = (mask << 1) + (1 if filter_bits > i else 0)
                if (value & mask) == (filter_ip & mask) :
                    return False
                else :
                    return True
        elif f_name == 'source-port' or f_name == 'destination-port' :
            # determine which kind of format the port is
                
                return True
        elif f_name == 'begin-time' or f_name == 'end-time' :
            try :
                index = self.filters.index(f_name)
                regex = self.filter_res[index]
            except ValueError :
                # no regex matching that, so accept
                return False
            # match f_value against time_re
            match = regex.match(f_value)
            if match == None :
                # no match, shouldn't happen, but just silently accept
                return False
            # get the values
            (year,month,day,hour,min,sec) = match.groups()

            # datetime-ify the input value
            v_dt = datetime.utcfromtimestamp(value)

            if f_name == 'begin-time' :
                # fill any unset parameters ("rounding down")
                year = int(year) if year != None else dt_date.min.year
                month = int(month) if month != None else dt_date.min.month
                day = int(day) if day != None else dt_date.min.day
                hour = int(hour) if hour != None else dt_time.min.hour
                min = int(min) if min != None else dt_time.min.minute
                sec = int(sec) if sec != None else dt_time.min.second
                # datetime-ify the filter value
                f_dt = datetime(year,month,day,hour,min,sec)
                # check the values
                if f_dt > v_dt :
                    # 'begin-time' is bigger than input value, so drop
                    return True
            elif f_name == 'end-time' :
                # fill any unset parameters ("rounding up")
                year = int(year) if year != None else dt_date.max.year
                month = int(month) if month != None else dt_date.max.month
                day = int(day) if day != None else dt_date.max.day
                hour = int(hour) if hour != None else dt_time.max.hour
                min = int(min) if min != None else dt_time.min.minute
                sec = int(sec) if sec != None else dt_time.max.second
                # datetime-ify the filter value
                f_dt = datetime(year,month,day,hour,min,sec)
                # check the values
                if f_dt < v_dt :
                    # 'end-time' is smaller than input value, so drop
                    return True
            return False
        elif f_name == 'latest' :
            now = datetime.utcnow()
            try :
                index = self.filters.index(f_name)
                regex = self.filter_res[index]
            except ValueError :
                # no regex matching that, so accept
                return False
            # match f_value against regex
            match = regex.match(f_value)
            if match == None :
                # no match, shouldn't happen, but just silently accept
                return False
            # get the value
            (nsecs,) = match.groups()
            latest_secs = timedelta(0, int(nsecs))
            then = datetime.utcfromtimestamp(value)
            if (now - latest_secs) >= then :
                return True
        return False

    # see if the item matches against a filter
    def do_filter(self, all_data) :
        new_data = [ ]

        # we'll go through all the all_data and filter out items we don't want
        for item in all_data :
            start_time = item['start-time']
            end_time = item['end-time']
            if self.filter_reject('end-time', end_time) :
                # skip this entire item (don't add to new_data)
                continue
            if self.filter_reject('latest', end_time) :
                # make sure this record ends within the past 'latest' seconds
                continue
            sessions = item['sessions']
            new_sessions = [ ]
            # each item has multiple data entries for a dump-/end-time
            for session in sessions :
                local_ip = session['local-ip']
                remote_ip = session['remote-ip']
                begin_time = session['begin-time']
                if self.filter_reject('local-ip', local_ip) :
                    continue
                if self.filter_reject('remote-ip', remote_ip) :
                    continue
                if self.filter_reject('begin-time', begin_time) :
                    continue
                # if it wasn't filtered out, then it is a valid session
                new_sessions.append(session)

            # now combine all the filtered data for a item
            new_item = { }
            new_item['start-time'] = start_time
            new_item['end-time'] = end_time
            new_item['size'] = item['size']
            new_item['sessions'] = new_sessions
            new_data.append(new_item)
        return new_data

    # should parse a file and add it to the all_data structure
    def add_file(self, file_name) :
        self.parser.clear_log()
        self.parser.parse(file_name)
        self.all_data.append(self.parser.get_log())
        self.cache['valid'] = False

    # derives a all_data list to a local->remote list
    # returns
    # { 'local-ip', 'remote-ip', 'all-ports', 'all-packets', 'all-octets',
    #   'tcp-ports', 'tcp-packets','tcp-octets', 'udp-ports', 'udp-packets',
    #   'udp-octets' }
    def derive_point_to_points(self, all_data) :
        # this is gonna be intense
        data = { } # we want to combine like point-to-point connections

        # we need to derive from every item in the all_data list
        for item in all_data :
            end_time = item['end-time']
            watch_data = item['watch-data']
            # each item has multiple data entries for a dump-/end-time
            for entry in watch_data :
                local_ip = self.int2ip(entry['local-ip'])
                remote_ip = self.int2ip(entry['remote-ip'])
                ntcp = entry['ntcp']
                nudp = entry['nudp']
                tcp_tuples = entry['tcp-tuples']
                udp_tuples = entry['udp-tuples']
                # each data entry has some number of tcp-/udp-tuples
                pkts = {'all':0,'tcp':0,'udp':0,}
                bytes = {'all':0,'tcp':0,'udp':0,}
                for tuple in tcp_tuples :
                    pkts['tcp'] += tuple['npkts-in'] + tuple['npkts-out']
                    bytes['tcp'] += tuple['nbytes-in'] + tuple['nbytes-out']
                    pkts['all'] += pkts['tcp']
                    bytes['all'] += bytes['tcp']
                for tuple in udp_tuples :
                    pkts['udp'] += tuple['npkts-in'] + tuple['npkts-out']
                    bytes['udp'] += tuple['nbytes-in'] + tuple['nbytes-out']
                    pkts['all'] += pkts['udp']
                    bytes['all'] += bytes['udp']
                # now combine all the data into the structure
                key = local_ip+'>'+remote_ip
                p2p = data.get(key, { })
                p2p['local-ip'] = local_ip
                p2p['remote-ip'] = remote_ip
                p2p['tcp-ports'] = p2p.get('tcp-ports', 0) + ntcp
                p2p['tcp-packets'] = p2p.get('tcp-packets', 0) + pkts['tcp']
                p2p['tcp-octets'] = p2p.get('tcp-octets', 0) + bytes['tcp']
                p2p['udp-ports'] = p2p.get('udp-ports', 0) + nudp
                p2p['udp-packets'] = p2p.get('udp-packets', 0) + pkts['udp']
                p2p['udp-octets'] = p2p.get('udp-octets', 0) + bytes['udp']
                p2p['all-ports'] = p2p.get('all-ports', 0) + ntcp + nudp
                p2p['all-packets'] = p2p.get('all-packets', 0) + pkts['all']
                p2p['all-octets'] = p2p.get('all-octets', 0) + bytes['all']
                data[key] = p2p
        return data.values()

    # derives a all_data list to a local-ip only list
    # returns
    # { 'local-ip', 'connections', 'sessions', 'all-packets', 'all-octets',
    #   'tcp-ports', 'tcp-packets','tcp-octets', 'udp-ports', 'udp-packets',
    #   'udp-octets' }
    def derive_local_ips(self, all_data) :
        #all_data = self.derive_point_to_points(all_data)
        data_names = ('tcp-ports','tcp-packets','tcp-octets',
                      'udp-ports','udp-packets','udp-octets',
                      'all-ports','all-packets','all-octets',)
        data = { }
        # go through all the raw data and combine local ips
        for item in all_data :
            local_ip = item['local-ip']
            entry = data.get(local_ip, {})
            entry['local-ip'] = local_ip
            entry['connections'] = entry.get('connections', 0) + 1
            # aggregate all information with this IP
            for name in data_names :
                # (tcp,udp,all)-(ports,packets,octets) incr by matching item
                entry[name] = entry.get(name, 0) + item[name]
            # connections increment every new item (remote-ip(?))
            data[local_ip] = entry
        # now rename all-ports -> sessions
        for local_ip in data :
            entry = data[local_ip]
            entry['sessions'] = entry['all-ports']
            del entry['all-ports']
        return data.values()

    def get_data(self, raw=False) :
        # based on the settings, return a tuple of data tuples
        key = self.settings['sort-key']
        threshold = self.settings['threshold']

        # see if any thing has changed
        if self.cache['key'] == key and self.cache['threshold'] == threshold :
            if self.cache['valid'] :
                c_filters = self.cache['filters']
                s_filters = self.settings['filters']
                valid = True
                for f in self.filters :
                    if f in c_filters and f in s_filters :
                        if c_filters[f] != s_filters[f] :
                            valid = False
                            break
                if valid :
                    return self.cache['data']

        # filter the all_data through any filters
        data = self.all_data
        data = self.do_filter(self.all_data)

        point_to_points = ('tcp-ports','tcp-packets','tcp-octets',
                           'udp-ports','udp-packets','udp-octets',
                           'all-ports','all-packets','all-octets',)
        local_ips = ('connections','sessions',)

        if raw == True :
            return data
        elif key == 'raw' :
            data = sorted(data, lambda l,r : cmp(r['end-time'], l['end-time']))
            flat_data = [ ]
            # bump every ip entry under an end-time to a top-level entry
            for d in data :
                end_time = d['end-time']
                end_time = time.strftime('%Y-%m-%d.%H:%M:%S', time.gmtime(end_time))
                watch_data = d['watch-data']
                for entry in watch_data :
                    flat_entry = {'end-time':end_time,}
                    for item in entry :
                        if item == 'local-ip' or item == 'remote-ip' :
                            flat_entry[item] = self.int2ip(entry[item])
                        else :
                            flat_entry[item] = entry[item]
                    flat_data.append(flat_entry)
            return flat_data
        elif key in local_ips :
            data = self.derive_point_to_points(data)
            data = self.derive_local_ips(data)
        elif key in point_to_points :
            data = self.derive_point_to_points(data)

        # make sure we exceed the threshold
        data = [ i for i in data if i[key] >= threshold ]
        # sort the data by a key
        data = sorted(data, lambda l,r : cmp(r[key], l[key]))

        self.cache['key'] = key
        self.cache['threshold'] = threshold
        for f in self.filters :
            self.cache['filters'] = self.settings['filters']
        self.cache['data'] = tuple(data)
        self.cache['valid'] = True

        return tuple(data)

    def get_fields(self, headers=True) :
        # based on the settings, return a tuple of display fields
        # the first five are displayed by default, the rest show on detail view
        key = self.settings['sort-key']
        tcp_fields = ('tcp-ports','tcp-packets','tcp-octets',)
        udp_fields = ('udp-ports','udp-packets','udp-octets',)
        all_fields = ('all-ports','all-packets','all-octets',)

        if key == 'raw' :
            fields = ['local-ip','remote-ip', 'end-time', 'ntcp', 'nudp', 'tcp-tuples','udp-tuples',]
        elif key == 'connections' or key == 'sessions' :
            fields = ['local-ip',]
            fields.extend(['connections','sessions',])
            fields.extend(list(all_fields[1:]))
            fields.extend(list(tcp_fields))
            fields.extend(list(udp_fields))
        else :
            fields = ['local-ip','remote-ip',]
            if headers == True :
                fields.extend(list(eval(key[0:3]+'_fields')))
            else :
                fields.extend(list(tcp_fields))
                fields.extend(list(udp_fields))
                fields.extend(list(all_fields))
        return tuple(fields)

    # convert an IP-as-integer to an IP-as-string
    def int2ip(self, addr) :
        octet = (addr>>24&0xff, addr>>16&0xff, addr>>8&0xff, addr&0xff)
        return '.'.join([ str(o) for o in octet ])

    # convert an IP-as-string to an IP-as-integer
    def ip2int(self, ip) :
        octets = ip.split('.')
        ip_int = int(octets[0])
        ip_int = (ip_int << 8) + int(octets[1])
        ip_int = (ip_int << 8) + int(octets[2])
        return (ip_int << 8) + int(octets[3])

    @classmethod
    def stringify(cls, str) :
        known_strings = {'sort-key':'Sort Key',
                         'local-ip':'Local IP',
                         'remote-ip':'Remote IP',
                         'source-port':'Source Port',
                         'destination-port':'Destination Port',
                         'begin-time':'Beginning Time',
                         'end-time':'Ending Time',
                         'tcp-tuples':'TCP Ports',
                         'udp-tuples':'UDP Ports',
                         'latest':'Latest Time',
                         'ntcp':'# TCP Ports',
                         'nudp':'# UDP Ports',
                         'tcp-ports':'# TCP Ports',
                         'tcp-packets':'# TCP Packets',
                         'tcp-octets':'# TCP Octets',
                         'udp-ports':'# UDP Ports',
                         'udp-packets':'# UDP Packets',
                         'udp-octets':'# UDP Octets',
                         'all-ports':'# All Ports',
                         'all-packets':'# All Packets',
                         'all-octets':'# All Octets',}
        if str in known_strings :
            return known_strings[str]
        else :
            return str.title()

def main() :
	print __version__

if __name__ == '__main__' :
	main()
