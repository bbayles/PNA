/* handle the hook and table insertions */

#include <linux/kernel.h>
#include <linux/percpu.h>
#include <linux/hash.h>

#include <linux/net.h>
#include <linux/netdevice.h>
#include <linux/socket.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <net/ip.h>
#include <linux/tcp.h>
#include <linux/udp.h>

#include "pna.h"

/* constants for this file */
#define PROBE_LIMIT 128
#define PERF_MEASURE 0

/* configuration settings */
char *pna_iface;
uint pna_prefix;
uint pna_mask;
uint pna_connections;
uint pna_sessions;
uint pna_tcp_ports;
uint pna_tcp_bytes;
uint pna_tcp_packets;
uint pna_udp_ports;
uint pna_udp_bytes;
uint pna_udp_packets;
uint pna_ports;
uint pna_bytes;
uint pna_packets;
uint pna_debug;

/* table meta-information */
struct utab_info *utab_info;

/* for performance measures */
#include <linux/jiffies.h>
struct pna_perf {
    __u64 t_jiffies; /* 8 */
    struct timeval currtime; /* 8 */
    struct timeval prevtime; /* 8 */
    __u32 p_interval[DIRECTIONS]; /* 8 */
    __u32 B_interval[DIRECTIONS]; /* 8 */
    char pad[64-24-2*sizeof(struct timeval)];
}; /* should total 64 bytes */
/* taken from linux/jiffies.h in kernel v2.6.21 */
#ifndef time_after_eq64
# define time_after_eq64(a,b) \
   (typecheck(__u64,a) && typecheck(__u64,b) && ((__s64)(a)-(__s64)(b)>=0))
#endif

/* union for l4 headers */
union l4hdr {
    struct tcphdr tcp;
    struct udphdr udp;
};

/* per-cpu data */
DEFINE_PER_CPU(int, utab_index);
DEFINE_PER_CPU(struct pna_perf *, perf_data);

/* Find and set/update the level 1 table entry */
struct lip_entry *do_lip_entry(uint local_ip, uint direction)
{
    struct utab_info *info;
    struct lip_entry *lip_entry;
    unsigned int i;
    unsigned int hash = hash_long(local_ip, PNA_LIP_BITS);

    /* get this table this CPU is using */
    info = &utab_info[get_cpu_var(utab_index)];

    /* loop through table until we find right entry */
    for ( i = 0; i < PROBE_LIMIT; i++ )
    {
        lip_entry = &info->lips[hash];

        /* check if IP is a match */
        if (local_ip == lip_entry->local_ip)
        {
        	return lip_entry;
        }

        /* check if IP is clear */
        if (0 == lip_entry->local_ip)
        {
            /* set up entry and return it */
            lip_entry->local_ip = local_ip;
            info->nlips++;
            return lip_entry;
        }

        hash = (hash + 1) % PNA_LIP_ENTRIES;
    }
   
    info->nlips_missed++;
    return (struct lip_entry *)NULL;
}

/* Find and set/update the level 2 table entry */
struct rip_entry *do_rip_entry(struct lip_entry *lip_entry,
					           uint remote_ip, uint direction)
{
    struct utab_info *info;
    struct rip_entry *rip_entry;
	nf_bitmap rip_bits;
    unsigned int i;
    unsigned int hash = lip_entry->local_ip ^ remote_ip;

    info = &utab_info[get_cpu_var(utab_index)];

	hash = hash_long(hash, PNA_RIP_BITS);

    /* loop through table until we find right entry */
    for ( i = 0; i < PROBE_LIMIT; i++ )
    {
        rip_entry = &info->rips[hash];
		rip_bits = lip_entry->dsts[hash/BITMAP_BITS];

        /* check for match */
        if ( remote_ip == rip_entry->remote_ip
			&& 0 != (rip_bits & (1 << hash % BITMAP_BITS)))
        {
			if ( 0 == (rip_entry->info_bits & (1 << direction)) )
			{
				/* we haven't seen this direction yet, add it */
				lip_entry->ndsts[direction]++;
				/* indicate that we've seen this direction */
				rip_entry->info_bits |= (1 << direction);
			}
            return rip_entry;
        }

        /* check for free spot */
        if ( 0 == rip_entry->remote_ip )
        {
            /* set index of src IP */
            lip_entry->dsts[hash/BITMAP_BITS] |= (1 << (hash % BITMAP_BITS));
            /* set all fields if a match */
            rip_entry->remote_ip = remote_ip;
            /* update the number of connections */
            lip_entry->ndsts[direction]++;
			/* indicate that we've seen this direction */
			rip_entry->info_bits |= (1 << direction);
            /* first time this remote IP was seen it was travelling ... */
			rip_entry->info_bits |= (1 << (direction + DIRECTIONS));

            info->nrips++;
            return rip_entry;
        }

        /* move to next entry */
        hash = (hash + 1) % PNA_RIP_ENTRIES;
    }

	info->nrips_missed++;
    return (struct rip_entry *)NULL;
}

/* Find and set/update the level 3 table entry */
struct port_entry *do_port_entry(struct lip_entry *lip_entry,
                                  struct rip_entry *rip_entry,
                                  ushort proto, ushort local_port,
                                  ushort remote_port, uint length, uint direction)
{
    struct utab_info *info;
	struct timeval timeval;
    struct port_entry *prt_entry;
    nf_bitmap prt_bits;
    unsigned int i, hash;
    u32 ports;

	info = &utab_info[get_cpu_var(utab_index)];
    
    /* hash on <(local_port << 16)|remote_port> */
    ports = rip_entry->remote_ip ^ ((remote_port << 16) | local_port);
    hash = hash_long(ports, PNA_PORT_BITS);

    /* loop through table until we find right entry */
    for ( i = 0; i < PROBE_LIMIT; i++ )
    {
        prt_entry = &(info->ports[proto][hash]);
        prt_bits = rip_entry->prts[proto][hash/BITMAP_BITS];

        /* check for match */
        if ( local_port == prt_entry->local_port
            && remote_port == prt_entry->remote_port
            && 0 != (prt_bits & (1 << hash%BITMAP_BITS)) )
        {
            rip_entry->nbytes[direction][proto] += length;
            rip_entry->npkts[direction][proto]++;
            prt_entry->nbytes[direction] += length;
            prt_entry->npkts[direction]++;

			if ( 0 == (prt_entry->info_bits & (1 << direction)) )
			{
				/* we haven't seen this direction yet, add it */
				rip_entry->nprts[direction][proto]++;
				/* indicate that we've seen this direction */
				prt_entry->info_bits |= (1 << direction);
			}
            return prt_entry;
        }

        /* check for free spot */
        if ( 0 == (prt_entry->local_port | prt_entry->remote_port) )
        {
            prt_entry->local_port = local_port;
            prt_entry->remote_port = remote_port;

            /* update all fields if a match */
            rip_entry->prts[proto][hash/BITMAP_BITS] |= (1 << hash%BITMAP_BITS);
            rip_entry->nbytes[direction][proto] += length;
            rip_entry->npkts[direction][proto]++;

			/* port specific information */
            prt_entry->nbytes[direction] += length;
            prt_entry->npkts[direction]++;
        	do_gettimeofday(&timeval);
			prt_entry->timestamp = timeval.tv_sec;

            rip_entry->nprts[direction][proto]++;
			/* indicate that we've seen this direction */
			prt_entry->info_bits |= (1 << direction);
            /* the first packet of a flow, mark the direction it came from */
			prt_entry->info_bits |= (1 << (direction + DIRECTIONS));

			/* also update the lip_entry because this is a new session */
			lip_entry->nsess[direction]++;

            info->nports++;
            return prt_entry;
        }

        /* move to next entry */
        hash = (hash + 1) % PNA_PORT_ENTRIES;
    }

	info->nports_missed++;
    return (struct port_entry *)NULL;
}

/*
 * Netfilter hook/setup/takedown functions
 */

/* Netfilter hook */
unsigned int nf_ses_watch_hook(unsigned int hooknum, 
                               struct sk_buff *skb,
                               const struct net_device *in,
                               const struct net_device *out,
                               int (*okfn)(struct sk_buff *))
{
    unsigned int ret;
    struct ethhdr *l2hdr;
    struct iphdr *l3hdr;
    union l4hdr *l4hdr;
    unsigned int iphdr_len;
    struct lip_entry *lip_entry;
    struct rip_entry *rip_entry;
    struct port_entry *prt_entry;
	uint direction, temp;
	uint local_ip, remote_ip;
    ushort local_port, remote_port, proto, pkt_len;
#if PERF_MEASURE == 1
    struct pna_perf *perf = get_cpu_var(perf_data);
#endif /* PERF_MEASURE == 1 */

    /* We have one function, snoop and log packets, Linux can ignore them */
    ret = NF_DROP;

    printk("got packet: %d %d %d\n", pna_prefix, pna_mask, pna_connections);
    return ret;

	/* We only accept if the `in` device is not one we care about */
	if ( 0 != strcmp(in->name, pna_iface))
	{
		return NF_ACCEPT;
	}

    /* grab the packet headers */
    l2hdr = (struct ethhdr *)skb_mac_header(skb);
    l3hdr = (struct iphdr *)skb_network_header(skb);
	pkt_len = ntohs(l3hdr->tot_len);

    /* (*skb)->h has not been set up yet. So we hack. */
    iphdr_len = l3hdr->ihl * sizeof(int);
    l4hdr = (union l4hdr *)((uintptr_t)(skb->data) + iphdr_len);

    /* grab transport specific information */
    switch (l3hdr->protocol)
    {
    case SOL_TCP:
        proto = PNA_PROTO_TCP;
        local_port = ntohs(l4hdr->tcp.source);
        remote_port = ntohs(l4hdr->tcp.dest);
        break;
    case SOL_UDP:
        proto = PNA_PROTO_UDP;
        local_port = ntohs(l4hdr->udp.source);
        remote_port = ntohs(l4hdr->udp.dest);
        break;
    default:
       /* If we don't recognize it, let Linux handle it */
       return NF_DROP;
    }

	/* Determine the nature of this packet (ingress or egress) */
	/* assume that we have a local packet first */
	local_ip = ntohl(l3hdr->saddr);

	/* check that it is local */
	temp = local_ip & pna_mask;
	if ( temp == pna_prefix )
	{
		/* saddr is local */
		// local_ip, local_port, remote_port are set
		remote_ip = ntohl(l3hdr->daddr);
		direction = PNA_DIR_OUTBOUND;
	}
	else
	{
		/* assume daddr is local */
		remote_ip = local_ip;
		local_ip = ntohl(l3hdr->daddr);

		temp = local_port;
		local_port = remote_port;
		remote_port = temp;
		direction = PNA_DIR_INBOUND;
	}

#if PERF_MEASURE == 1
    /**********************************
     *   PERFORMANCE EVALUTION CODE   *
     **********************************/
    /* time_after_eq64(a,b) returns true if time a >= time b. */
    if ( time_after_eq64(get_jiffies_64(), perf->t_jiffies) )
    {
        __u32 t_interval;
        __u32 kpps_in, Mbps_in, avg_in;
        __u32 kpps_out, Mbps_out, avg_out;

        /* get sampling interval time */
        do_gettimeofday(&perf->currtime);
        t_interval = perf->currtime.tv_sec - perf->prevtime.tv_sec;
    /* update for next round */
        perf->prevtime = perf->currtime;

        /* calculate the numbers */
        kpps_in = perf->p_interval[INBOUND] / 1000 / t_interval;
		/* 125000 Mb = (1000 MB/KB * 1000 KB/B) / 8 bits/B */
        Mbps_in = perf->B_interval[INBOUND] / 125000 / t_interval;
		if (perf->p_interval[INBOUND] != 0)
		{
			avg_in = (perf->B_interval[INBOUND]/perf->p_interval[INBOUND])-INTERFRAME_GAP;
		}
		else
		{
			avg_in = 0;
		}

        kpps_out = perf->p_interval[OUTBOUND] / 1000 / t_interval;
		/* 125000 Mb = (1000 MB/KB * 1000 KB/B) / 8 bits/B */
        Mbps_out = perf->B_interval[OUTBOUND] / 125000 / t_interval;
		if (perf->p_interval[OUTBOUND] != 0)
		{
			avg_out = (perf->B_interval[OUTBOUND]/perf->p_interval[OUTBOUND])-INTERFRAME_GAP;
		}
		else
		{
			avg_out = 0;
		}

        /* report the numbers */
        if (kpps_in + kpps_out > 0)
        {
			printk("pna_mod: hit in:{kpps:%u,Mbps:%u,avg:%u} out:{kpps:%u,Mbps:%u,avg:%u}\n",
					kpps_in, Mbps_in, avg_in, kpps_out, Mbps_out, avg_out);
			printk("on processor %d\n", smp_processor_id());
        }

        /* set updated counters */
        perf->p_interval[INBOUND] = 0;
        perf->B_interval[INBOUND] = 0;
        perf->p_interval[OUTBOUND] = 0;
        perf->B_interval[OUTBOUND] = 0;
        perf->t_jiffies = msecs_to_jiffies(LOGSLEEP*MSEC_PER_SEC);
        perf->t_jiffies += get_jiffies_64();
    }

    /* increment packets seen in this interval */
    perf->p_interval[direction]++;
    perf->B_interval[direction] += pkt_len + ETH_HDR_LEN + INTERFRAME_GAP;
    /**********************************
     * END PERFORMANCE EVALUTION CODE *
     **********************************/
#endif /* PERF_MEASURE == 1 */

    /* We'll let Linux handle ARP packets from monitored ports */
    if ((ETH_P_ARP == htons(l2hdr->h_proto)))
    {
        return NF_DROP;
    }

	/* make sure the local IP is one of interest */
	temp = local_ip & pna_mask;
	if ( temp != pna_prefix )
	{
		/* don't monitor this packet, but don't let Linux see it either */
		return NF_DROP;
	}

    /* find the entry beginning this connection*/
    lip_entry = do_lip_entry(local_ip, direction);
    if ( NULL == lip_entry )
    {
		if (pna_debug) printk("detected full source table\n");
        return ret;
    }
    else if ( lip_entry->ndsts[PNA_DIR_OUTBOUND] >= pna_connections )
	{
		/* host is trying to connect to too many destinations, ignore */
		session_action(PNA_MSG_BLOCK, local_ip, "too many connections");
        return ret;
	}

    /* find the entry completing this connection */
    rip_entry = do_rip_entry(lip_entry, remote_ip, direction);
    if ( NULL == rip_entry )
    {
		/* destination table is *FULL,* can't do anything */
		if (pna_debug) printk("detected full destination table\n");
        return ret;
    }
    else if ( rip_entry->nprts[PNA_DIR_OUTBOUND][proto] >= pna_ports )
	{
		/* host is creating too many unique sessions, ignore */
		session_action(PNA_MSG_BLOCK, local_ip, "too many ports");
        return ret;
	}
	else if ( rip_entry->nbytes[PNA_DIR_OUTBOUND][proto] >= pna_bytes )
	{
		/* host has surpassed a huge amount of protocol bandwidth, ignore */
		session_action(PNA_MSG_BLOCK, local_ip, "too many bytes");
        return ret;
	}
	else if ( rip_entry->npkts[PNA_DIR_OUTBOUND][proto] >= pna_packets )
	{
		/* host has surpassed a huge amount of protocol traffic, ignore */
		session_action(PNA_MSG_BLOCK, local_ip, "too many packets");
        return ret;
	}
	/* XXX: might also want to do ALL thresholds */

    /* update the session entry */
	prt_entry = do_port_entry(lip_entry, rip_entry, proto, local_port,
			                 remote_port, pkt_len, direction);
    if ( NULL == prt_entry )
    {
		if (pna_debug) printk("detected full port table\n");
        return ret;
    }
	else if ( lip_entry->nsess[PNA_DIR_OUTBOUND] >= pna_sessions )
	{
		/* host is trying to connect to too many destinations, ignore */
		session_action(PNA_MSG_BLOCK, local_ip, "too many sessions");
        return ret;
	}
	else if ( lip_entry->nsess[PNA_DIR_INBOUND] >= pna_sessions )
	{
		/* host is trying to connect to too many destinations, ignore */
		session_action(PNA_MSG_WHITELIST, local_ip, "external scan");
        return ret;
	}

    return ret;
}
