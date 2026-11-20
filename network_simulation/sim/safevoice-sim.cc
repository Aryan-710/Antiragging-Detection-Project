/*
 * SafeVoice network simulation (ns-3)
 * ===================================
 * Compares three ways of carrying severity-tagged SOS packets inside a hostel:
 *   A  PROPOSED : 802.11n ad-hoc mesh + AODV + severity priority (EDCA and/or queue level)
 *   B  BASELINE : same ad-hoc mesh + AODV, no priority (everything best effort, FIFO)
 *   C  CURRENT  : infrastructure Wi-Fi (8 APs, two per floor, one orthogonal channel each, wired CSMA backhaul to a
 *                 campus server), no priority, same background congestion.
 *
 * Everything random about the *workload* (positions, mobility, SOS arrivals and severities,
 * background flows, failure sets) is read from a workload file produced by
 * tools/gen_workload.py, so A, B and C see exactly the same inputs.  The FNV-1a-64 hash of
 * that file is logged per run.
 *
 * Priority mechanisms (scenario A):
 *   - MAC level  : severity -> IP ToS -> 802.11e access category (3->AC_VO, 2->AC_VI,
 *                  1->AC_BE, background->AC_BE or AC_BK).
 *   - Queue level: SvPriorityQueueDisc (strict priority by severity band, push-out of the
 *                  lowest-severity packet when full).  Severity is read from the UDP
 *                  destination port (7001/7002/7003) so it is independent of the ToS field.
 *   --priorityMode = none | edca | queue | both
 *
 * Build: see sim/build.sh.   Unit test of the queue disc:  safevoice-sim --unitTest=1
 */
#include "ns3/aodv-module.h"
#include "ns3/aodv-packet.h"
#include "ns3/applications-module.h"
#include "ns3/bridge-module.h"
#include "ns3/core-module.h"
#include "ns3/csma-module.h"
#include "ns3/drop-tail-queue.h"
#include "ns3/dsdv-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/ipv4-queue-disc-item.h"
#include "ns3/llc-snap-header.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/propagation-module.h"
#include "ns3/sta-wifi-mac.h"
#include "ns3/traffic-control-module.h"
#include "ns3/udp-header.h"
#include "ns3/wifi-mpdu.h"
#include "ns3/wifi-module.h"
#include "ns3/yans-wifi-channel.h"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <unordered_map>
#include <vector>

using namespace ns3;
NS_LOG_COMPONENT_DEFINE("SafeVoiceSim");

// ---------------------------------------------------------------------------------------------
// Geometry (must match tools/gen_workload.py)
// ---------------------------------------------------------------------------------------------
static const double kFloorH = 3.0;            // m
static const double kCorrY0 = 6.0, kCorrY1 = 9.0;
static const double kRoomW = 40.0 / 6.0;      // 6 rooms per side of the corridor

// Ports used to carry severity / traffic type
static const uint16_t kPortWarm = 7000;       // warm-up pings (not SOS)
static const uint16_t kPortSos = 7000;        // SOS severity s uses port 7000+s (s = 1..3)
static const uint16_t kPortBg = 8000;         // background traffic
static const uint16_t kPortAodv = 654, kPortDsdv = 269;

static uint64_t
Fnv1a64(const std::string& s)
{
    uint64_t h = 0xcbf29ce484222325ULL;
    for (unsigned char c : s)
    {
        h ^= c;
        h *= 0x100000001b3ULL;
    }
    return h;
}

// ---------------------------------------------------------------------------------------------
// Application payload header: every per-packet statistic comes from here (not from tags).
// ---------------------------------------------------------------------------------------------
class SvHeader : public Header
{
  public:
    uint32_t seq = 0;      // SOS: index in workload SOS list; BG: packet counter
    uint64_t txNs = 0;     // transmit time
    uint8_t sev = 0;       // severity (1..3) for SOS, 0 otherwise
    uint8_t kind = 0;      // 0 = SOS, 1 = background, 2 = warm-up ping
    uint16_t src = 0;      // source student id
    uint32_t flow = 0;     // background flow id

    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::SvHeader").SetParent<Header>().AddConstructor<SvHeader>();
        return tid;
    }
    TypeId GetInstanceTypeId() const override { return GetTypeId(); }
    uint32_t GetSerializedSize() const override { return 20; }
    void Serialize(Buffer::Iterator i) const override
    {
        i.WriteHtonU32(seq);
        i.WriteHtonU64(txNs);
        i.WriteU8(sev);
        i.WriteU8(kind);
        i.WriteHtonU16(src);
        i.WriteHtonU32(flow);
    }
    uint32_t Deserialize(Buffer::Iterator i) override
    {
        seq = i.ReadNtohU32();
        txNs = i.ReadNtohU64();
        sev = i.ReadU8();
        kind = i.ReadU8();
        src = i.ReadNtohU16();
        flow = i.ReadNtohU32();
        return 20;
    }
    void Print(std::ostream& os) const override { os << "seq=" << seq << " sev=" << int(sev); }
};
NS_OBJECT_ENSURE_REGISTERED(SvHeader);

// ---------------------------------------------------------------------------------------------
// Deterministic, symmetric indoor loss model: extra loss per floor slab and per wall crossed.
// Chained after ns-3's LogDistancePropagationLossModel.
//   floors crossed = |floor(z_a/3) - floor(z_b/3)|
//   walls crossed  = analytic count on the horizontal projection of the straight line:
//     corridor walls y=6 and y=9, and the 5 room-partition walls (x = k*40/6) that only exist
//     inside the room bands (y<6 or y>9).
// ---------------------------------------------------------------------------------------------
class SvWallFloorLossModel : public PropagationLossModel
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid =
            TypeId("ns3::SvWallFloorLossModel")
                .SetParent<PropagationLossModel>()
                .AddConstructor<SvWallFloorLossModel>()
                .AddAttribute("WallLoss", "dB per wall", DoubleValue(5.0),
                              MakeDoubleAccessor(&SvWallFloorLossModel::m_wall), MakeDoubleChecker<double>())
                .AddAttribute("FloorLoss", "dB per floor slab", DoubleValue(13.0),
                              MakeDoubleAccessor(&SvWallFloorLossModel::m_floor), MakeDoubleChecker<double>());
        return tid;
    }
    static int CountWalls(const Vector& p, const Vector& q)
    {
        int c = 0;
        for (double yw : {kCorrY0, kCorrY1})
        {
            if ((p.y - yw) * (q.y - yw) < 0) ++c;
        }
        if (std::fabs(q.x - p.x) > 1e-9)
        {
            for (int k = 1; k <= 5; ++k)
            {
                double xw = k * kRoomW;
                if ((p.x - xw) * (q.x - xw) < 0)
                {
                    double y = p.y + (q.y - p.y) * (xw - p.x) / (q.x - p.x);
                    if (y < kCorrY0 || y > kCorrY1) ++c;
                }
            }
        }
        return c;
    }
    static int FloorOf(double z) { return static_cast<int>(std::floor(z / kFloorH)); }

  private:
    double DoCalcRxPower(double txPowerDbm, Ptr<MobilityModel> a, Ptr<MobilityModel> b) const override
    {
        Vector pa = a->GetPosition(), pb = b->GetPosition();
        int floors = std::abs(FloorOf(pa.z) - FloorOf(pb.z));
        return txPowerDbm - m_wall * CountWalls(pa, pb) - m_floor * floors;
    }
    int64_t DoAssignStreams(int64_t) override { return 0; } // deterministic: no random streams
    double m_wall = 5.0, m_floor = 13.0;
};
NS_OBJECT_ENSURE_REGISTERED(SvWallFloorLossModel);

// ---------------------------------------------------------------------------------------------
// Severity-band strict-priority queue disc with push-out.
//   band 3 = SOS sev 3 (critical) ... band 1 = SOS sev 1, band 0 = everything else (background,
//   routing control).  Non-IP packets (ARP) go to band 3 so address resolution is never starved.
//   Dequeue: highest non-empty band first (FIFO inside a band).
//   Enqueue when full: if a STRICTLY lower non-empty band exists, drop the oldest packet of the
//   lowest non-empty band (push-out) and accept the new packet; otherwise drop the new packet.
//   (ns-3's PrioQueueDisc does NOT push out; this class does.)
// ---------------------------------------------------------------------------------------------
class SvPriorityQueueDisc : public QueueDisc
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid =
            TypeId("ns3::SvPriorityQueueDisc")
                .SetParent<QueueDisc>()
                .SetGroupName("TrafficControl")
                .AddConstructor<SvPriorityQueueDisc>()
                .AddAttribute("MaxSize", "Max queue disc size (packets)", QueueSizeValue(QueueSize("100p")),
                              MakeQueueSizeAccessor(&QueueDisc::SetMaxSize, &QueueDisc::GetMaxSize),
                              MakeQueueSizeChecker());
        return tid;
    }
    SvPriorityQueueDisc()
        : QueueDisc(QueueDiscSizePolicy::MULTIPLE_QUEUES)
    {
    }
    ~SvPriorityQueueDisc() override {}

    static int BandOf(Ptr<const QueueDiscItem> item)
    {
        Ptr<const Ipv4QueueDiscItem> ip = DynamicCast<const Ipv4QueueDiscItem>(item);
        if (!ip) return 3;
        if (ip->GetHeader().GetProtocol() != 17) return 0;
        UdpHeader u;
        if (ip->GetPacket()->GetSize() < u.GetSerializedSize()) return 0;
        ip->GetPacket()->PeekHeader(u);
        uint16_t p = u.GetDestinationPort();
        if (p >= kPortSos + 1 && p <= kPortSos + 3) return p - kPortSos;
        return 0;
    }

  private:
    bool DoEnqueue(Ptr<QueueDiscItem> item) override
    {
        int band = BandOf(item);
        if (GetNPackets() + 1 > GetMaxSize().GetValue())
        {
            int lowest = -1;
            for (int b = 0; b < band; ++b)
            {
                if (!GetInternalQueue(b)->IsEmpty()) { lowest = b; break; }
            }
            if (lowest < 0)
            {
                DropBeforeEnqueue(item, "Queue disc limit exceeded"); // tail drop of the arrival
                return false;
            }
            Ptr<QueueDiscItem> victim = GetInternalQueue(lowest)->Dequeue();
            DropAfterDequeue(victim, "Push-out drop of lower severity");
        }
        if (!GetInternalQueue(band)->Enqueue(item))
        {
            DropBeforeEnqueue(item, "Internal queue enqueue failed");
            return false;
        }
        return true;
    }
    Ptr<QueueDiscItem> DoDequeue() override
    {
        for (int b = 3; b >= 0; --b)
        {
            if (!GetInternalQueue(b)->IsEmpty()) return GetInternalQueue(b)->Dequeue();
        }
        return nullptr;
    }
    Ptr<const QueueDiscItem> DoPeek() override
    {
        for (int b = 3; b >= 0; --b)
        {
            if (!GetInternalQueue(b)->IsEmpty()) return GetInternalQueue(b)->Peek();
        }
        return nullptr;
    }
    bool CheckConfig() override
    {
        if (GetNQueueDiscClasses() > 0) return false;
        if (GetMaxSize().GetUnit() != QueueSizeUnit::PACKETS) return false;
        if (GetNInternalQueues() == 0)
        {
            for (int i = 0; i < 4; ++i)
                AddInternalQueue(CreateObjectWithAttributes<DropTailQueue<QueueDiscItem>>(
                    "MaxSize", QueueSizeValue(GetMaxSize())));
        }
        return GetNInternalQueues() == 4;
    }
    void InitializeParams() override {}
};
NS_OBJECT_ENSURE_REGISTERED(SvPriorityQueueDisc);

// ---------------------------------------------------------------------------------------------
// Unit test for the queue disc: dequeue order + push-out behaviour.
// ---------------------------------------------------------------------------------------------
static std::map<std::string, int> g_utDrops;
static Ptr<Ipv4QueueDiscItem>
MakeItem(uint16_t dport, uint32_t id)
{
    Ptr<Packet> p = Create<Packet>(id); // payload size doubles as packet identity
    UdpHeader u;
    u.SetSourcePort(1234);
    u.SetDestinationPort(dport);
    p->AddHeader(u);
    Ipv4Header ip;
    ip.SetProtocol(17);
    ip.SetPayloadSize(p->GetSize());
    return Create<Ipv4QueueDiscItem>(p, Address(), 0x0800, ip);
}
static int
RunUnitTest()
{
    int fails = 0;
    auto check = [&](bool ok, const std::string& what) {
        std::printf("[UNITTEST] %s : %s\n", ok ? "PASS" : "FAIL", what.c_str());
        if (!ok) ++fails;
    };
    Ptr<SvPriorityQueueDisc> q = CreateObject<SvPriorityQueueDisc>();
    q->SetMaxSize(QueueSize("4p"));
    q->Initialize();
    q->TraceConnectWithoutContext("DropBeforeEnqueue",
        MakeCallback(+[](Ptr<const QueueDiscItem>, const char* r) { g_utDrops[r]++; }));
    q->TraceConnectWithoutContext("DropAfterDequeue",
        MakeCallback(+[](Ptr<const QueueDiscItem>, const char* r) { g_utDrops[r]++; }));
    // ids: 101,102 background(band0)  103 sev1  104 sev2  105 sev3  106 bg  107 sev1
    check(q->Enqueue(MakeItem(8000, 101)), "enqueue bg#101");
    check(q->Enqueue(MakeItem(8000, 102)), "enqueue bg#102");
    check(q->Enqueue(MakeItem(7001, 103)), "enqueue sev1#103");
    check(q->Enqueue(MakeItem(7002, 104)), "enqueue sev2#104 (queue now full, 4 pkts)");
    check(q->GetNPackets() == 4, "queue holds 4 packets");
    check(q->Enqueue(MakeItem(7003, 105)), "enqueue sev3#105 accepted by pushing out bg#101");
    check(!q->Enqueue(MakeItem(8000, 106)), "bg#106 arrival dropped (nothing lower to push out)");
    check(q->Enqueue(MakeItem(7001, 107)), "enqueue sev1#107 accepted by pushing out bg#102");
    check(q->GetNPackets() == 4, "queue still holds 4 packets");
    std::vector<uint32_t> order;
    for (int i = 0; i < 4; ++i)
    {
        Ptr<QueueDiscItem> it = q->Dequeue();
        if (!it) break;
        order.push_back(it->GetPacket()->GetSize() - 8); // minus UDP header
    }
    check(order == std::vector<uint32_t>({105, 104, 103, 107}),
          "dequeue order = sev3#105, sev2#104, sev1#103, sev1#107 (strict priority, FIFO within band)");
    check(q->Dequeue() == nullptr, "queue empty after 4 dequeues");
    int pushout = 0, overlimit = 0;
    for (auto& kv : g_utDrops)
    {
        if (kv.first.find("Push-out") != std::string::npos) pushout += kv.second;
        if (kv.first.find("limit exceeded") != std::string::npos) overlimit += kv.second;
    }
    check(pushout == 2, "exactly 2 push-out drops (bg#101, bg#102)");
    check(overlimit == 1, "exactly 1 arrival drop (bg#106)");
    std::printf("[UNITTEST] %s (%d failure(s))\n", fails == 0 ? "ALL PASSED" : "FAILED", fails);
    return fails == 0 ? 0 : 1;
}

// ---------------------------------------------------------------------------------------------
// Global simulation state
// ---------------------------------------------------------------------------------------------
struct NodeSpec { double x, y, z; int mobile; };
struct Wpt { double t, x, y, z; };
struct SosEv { double t; uint32_t src; int sev; int burst; };
struct BgSeg { uint32_t flow, src, dst, size; double rate, t0, t1; int kind; };
struct SosRec { double tx = -1; double rx = -1; int hops = -1; bool valid = false; };

static uint32_t g_n = 0;                       // number of students; desk/server id = g_n
static double g_warm = 10, g_end = 210;
static std::vector<NodeSpec> g_nodes;
static std::map<uint32_t, std::vector<Wpt>> g_wpts;
static std::vector<SosEv> g_sos;
static std::vector<std::pair<double, uint32_t>> g_warmPings;
static std::vector<BgSeg> g_bg;
static std::vector<uint32_t> g_failNodes;
static int g_failAp = 0;

static std::vector<Ptr<Socket>> g_tx;          // one UDP sender socket per student
static std::vector<Ipv4Address> g_addr;        // address of every student + desk
static std::vector<bool> g_dead;
static std::vector<SosRec> g_rec;
static std::set<uint32_t> g_warmReached;
static uint32_t g_warmReachedAtWarm = 0, g_assocAtWarm = 0;
static uint64_t g_bgTxBytes = 0, g_bgRxBytes = 0, g_sosTxBytes = 0, g_sosRxBytes = 0;
static uint64_t g_ctrlPkts = 0, g_ctrlBytes = 0;
static uint64_t g_aodvType[6] = {0, 0, 0, 0, 0, 0};   // [1]=RREQ [2]=RREP(non-hello) [3]=RERR [4]=RREP-ACK [5]=HELLO
static uint64_t g_origSev[4][1024];            // [sev][node] SOS originated (for relay analysis)
static std::map<std::string, uint64_t> g_tcDrop, g_tcDropSos;    // reason -> count
static uint64_t g_macDrop[4][5];               // [reason][band 0..3, 4=non-IP/unknown]
static double g_rxSensLogged = 0;
static uint64_t g_noRoute = 0, g_ipDropOther = 0, g_phyTx = 0, g_ctrlAll = 0;
static bool g_edca = false;
static bool g_bgBk = false;

static inline bool InWindow(double t) { return t >= g_warm && t < g_end; }

static uint8_t
TosFor(int kind, int sev)
{
    if (!g_edca) return 0;
    if (kind == 0) return sev == 3 ? 0xC0 : (sev == 2 ? 0xA0 : 0x00); // CS6->AC_VO, CS5->AC_VI, BE
    if (kind == 1) return g_bgBk ? 0x20 : 0x00;                        // CS1 -> AC_BK or BE
    return 0x00;
}

static void
SendPkt(uint32_t src, uint32_t dst, uint16_t dport, uint32_t size, int kind, int sev,
        uint32_t seq, uint32_t flow)
{
    if (g_dead[src]) return;
    SvHeader h;
    h.seq = seq; h.txNs = Simulator::Now().GetNanoSeconds(); h.sev = sev; h.kind = kind;
    h.src = src; h.flow = flow;
    Ptr<Packet> p = Create<Packet>(size - h.GetSerializedSize());
    p->AddHeader(h);
    g_tx[src]->SetIpTos(TosFor(kind, sev));
    g_tx[src]->SendTo(p, 0, InetSocketAddress(g_addr[dst], dport));
}

static void
DoSos(uint32_t idx)
{
    const SosEv& e = g_sos[idx];
    if (g_dead[e.src]) return;                 // a dead phone generates no SOS
    g_rec[idx].valid = true;
    g_rec[idx].tx = Simulator::Now().GetSeconds();
    g_origSev[e.sev][e.src]++;
    g_sosTxBytes += 160;
    SendPkt(e.src, g_n, kPortSos + e.sev, 160, 0, e.sev, idx, 0);
}

static void
DoWarm(uint32_t src) { SendPkt(src, g_n, kPortWarm, 64, 2, 0, 0, 0); }

static void
BgTick(uint32_t segIdx, uint32_t counter)
{
    const BgSeg& s = g_bg[segIdx];
    double now = Simulator::Now().GetSeconds();
    if (now >= s.t1 || now >= g_end) return;
    if (!g_dead[s.src])
    {
        if (InWindow(now)) g_bgTxBytes += s.size;
        SendPkt(s.src, s.dst, kPortBg, s.size, 1, 0, counter, s.flow);
    }
    Simulator::Schedule(Seconds(s.size * 8.0 / s.rate), &BgTick, segIdx, counter + 1);
}

static void
OnRecv(Ptr<Socket> sock)
{
    Address from;
    while (Ptr<Packet> p = sock->RecvFrom(from))
    {
        SvHeader h;
        p->PeekHeader(h);
        double now = Simulator::Now().GetSeconds();
        SocketIpTtlTag ttl;
        int hops = p->PeekPacketTag(ttl) ? (64 - int(ttl.GetTtl()) + 1) : -1;
        if (h.kind == 0)
        {
            if (h.seq < g_rec.size() && g_rec[h.seq].rx < 0)
            {
                g_rec[h.seq].rx = now;
                g_rec[h.seq].hops = hops;
                if (InWindow(now)) g_sosRxBytes += 160;
            }
        }
        else if (h.kind == 1)
        {
            if (InWindow(now)) g_bgRxBytes += p->GetSize();
        }
        else
        {
            g_warmReached.insert(h.src);
        }
    }
}

// ---- trace sinks ------------------------------------------------------------------------------
static void
OnTcDrop(Ptr<const QueueDiscItem> item, const char* reason)
{
    g_tcDrop[reason]++;
    if (SvPriorityQueueDisc::BandOf(item) >= 1 && DynamicCast<const Ipv4QueueDiscItem>(item))
        g_tcDropSos[reason]++;
}

static void
OnMacDrop(WifiMacDropReason reason, Ptr<const WifiMpdu> mpdu)
{
    int band = 4;
    Ptr<Packet> p = mpdu->GetPacket()->Copy();
    if (p->GetSize() > 8 + 20 + 8)
    {
        LlcSnapHeader llc;
        p->RemoveHeader(llc);
        if (llc.GetType() == 0x0800)
        {
            Ipv4Header ip;
            p->RemoveHeader(ip);
            band = 0;
            if (ip.GetProtocol() == 17)
            {
                UdpHeader u;
                p->PeekHeader(u);
                uint16_t d = u.GetDestinationPort();
                if (d >= kPortSos + 1 && d <= kPortSos + 3) band = d - kPortSos;
            }
        }
    }
    int r = std::min<int>(int(reason), 3);
    g_macDrop[r][band]++;
}

static void
OnSendOutgoing(const Ipv4Header& h, Ptr<const Packet> p, uint32_t)
{
    if (h.GetProtocol() != 17 || p->GetSize() < 8) return;
    UdpHeader u;
    p->PeekHeader(u);
    if (u.GetDestinationPort() == kPortAodv || u.GetDestinationPort() == kPortDsdv)
    {
        double now = Simulator::Now().GetSeconds();
        if (InWindow(now))
        {
            g_ctrlPkts++; g_ctrlBytes += p->GetSize() + 20;
            if (u.GetDestinationPort() == kPortAodv)
            {
                Ptr<Packet> c = p->Copy();
                c->RemoveHeader(u);
                aodv::TypeHeader th(aodv::AODVTYPE_RREQ);
                c->PeekHeader(th);
                int t = int(th.Get());
                if (t == 2 && h.GetTtl() == 1) t = 5;            // HELLO = RREP with TTL 1
                if (t >= 1 && t <= 5) g_aodvType[t]++;
            }
        }
    }
}

static void
OnPhyTx(Ptr<const Packet>, double) { g_phyTx++; }

static void
OnIpDrop(const Ipv4Header&, Ptr<const Packet>, Ipv4L3Protocol::DropReason r, Ptr<Ipv4>, uint32_t)
{
    if (r == Ipv4L3Protocol::DROP_NO_ROUTE) g_noRoute++;
    else g_ipDropOther++;
}

// ---------------------------------------------------------------------------------------------
// Workload file loader
// ---------------------------------------------------------------------------------------------
static uint64_t
LoadWorkload(const std::string& fn)
{
    std::ifstream f(fn, std::ios::binary);
    NS_ABORT_MSG_IF(!f, "cannot open workload " << fn);
    std::stringstream ss;
    ss << f.rdbuf();
    std::string all = ss.str();
    uint64_t hash = Fnv1a64(all);
    std::istringstream in(all);
    std::string line;
    std::map<std::string, double> param;
    while (std::getline(in, line))
    {
        std::istringstream ls(line);
        std::string tag;
        ls >> tag;
        if (tag == "PARAM") { std::string k; double v; ls >> k >> v; param[k] = v; }
        else if (tag == "NODE") { uint32_t id; NodeSpec s; ls >> id >> s.x >> s.y >> s.z >> s.mobile;
            if (g_nodes.size() <= id) g_nodes.resize(id + 1); g_nodes[id] = s; }
        else if (tag == "WPT") { uint32_t id; Wpt w; ls >> id >> w.t >> w.x >> w.y >> w.z; g_wpts[id].push_back(w); }
        else if (tag == "SOS") { SosEv e; ls >> e.t >> e.src >> e.sev >> e.burst; g_sos.push_back(e); }
        else if (tag == "WARM") { double t; uint32_t s; ls >> t >> s; g_warmPings.push_back({t, s}); }
        else if (tag == "BG") { BgSeg b; ls >> b.flow >> b.src >> b.dst >> b.size >> b.rate >> b.t0 >> b.t1 >> b.kind; g_bg.push_back(b); }
        else if (tag == "FAILNODE") { uint32_t i; ls >> i; g_failNodes.push_back(i); }
        else if (tag == "FAILAP") { ls >> g_failAp; }
    }
    g_n = static_cast<uint32_t>(param["n"]);
    g_warm = param["warmup"];
    g_end = param["warmup"] + param["duration"];
    NS_ABORT_MSG_IF(g_nodes.size() != g_n + 1, "workload node count mismatch");
    return hash;
}

static std::string
Hex16(uint64_t v) { char b[32]; std::snprintf(b, sizeof b, "%016llx", (unsigned long long)v); return b; }

// ---------------------------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------------------------
int
main(int argc, char* argv[])
{
    std::string scenario = "A", workload, outDir = ".", prio = "", routing = "aodv", failure = "none",
                bgAc = "be", mcsStr = "HtMcs3";
    uint32_t run = 1, tcQ = 100, macQ = 100, flowmon = 0, acStats = 0, unit = 0, nAps = 8, progress = 0, nonUniSame = 1, aodvHello = 1, aodvDestOnly = 1, aodvGrat = 1;
    double txPower = 16.0206, ple = 3.0, refLoss = 46.6777, wallDb = 5.0, floorDb = 13.0,
           failTime = 100.0, macDelay = 5.0, backhaulDelayMs = 0.1, rxSens = -92.0;
    std::string backhaulRate = "1Gbps", uplinkRate = "";
    CommandLine cmd(__FILE__);
    cmd.AddValue("scenario", "A (proposed), B (ad-hoc no priority), C (infrastructure Wi-Fi)", scenario);
    cmd.AddValue("workload", "workload file from tools/gen_workload.py", workload);
    cmd.AddValue("outDir", "output directory", outDir);
    cmd.AddValue("priorityMode", "none|edca|queue|both (default: A=both, B/C=none)", prio);
    cmd.AddValue("routing", "aodv|dsdv (ad-hoc scenarios)", routing);
    cmd.AddValue("failure", "none|node (ad-hoc: 10% nodes) |ap (scenario C: one AP)", failure);
    cmd.AddValue("failTime", "failure time [s]", failTime);
    cmd.AddValue("run", "RngRun (MAC backoff / routing jitter streams)", run);
    cmd.AddValue("txPower", "TX power dBm (same in all scenarios)", txPower);
    cmd.AddValue("nonUnicastSame", "1 = send broadcast frames at the data MCS (avoids the gray zone: AODV finds only links that carry data)", nonUniSame);
    cmd.AddValue("aodvHello", "AODV HELLO messages (0 = rely on link-layer feedback only)", aodvHello);
    cmd.AddValue("aodvDestOnly", "AODV DestinationOnly flag (1 = only the destination answers RREQs)", aodvDestOnly);
    cmd.AddValue("aodvGrat", "AODV gratuitous RREP", aodvGrat);
    cmd.AddValue("rxSens", "PHY RxSensitivity dBm (same in all scenarios; ns-3 default is -101)", rxSens);
    cmd.AddValue("mcs", "fixed HT MCS data mode, e.g. HtMcs3", mcsStr);
    cmd.AddValue("pathLossExp", "log-distance exponent", ple);
    cmd.AddValue("refLoss", "log-distance reference loss at 1 m [dB]", refLoss);
    cmd.AddValue("wallLoss", "dB per wall", wallDb);
    cmd.AddValue("floorLoss", "dB per floor slab", floorDb);
    cmd.AddValue("tcQueue", "queue disc limit per access category [packets]", tcQ);
    cmd.AddValue("macQueue", "Wi-Fi MAC queue limit per AC [packets]", macQ);
    cmd.AddValue("macDelay", "Wi-Fi MAC queue max packet lifetime [s]", macDelay);
    cmd.AddValue("bgAc", "background access category when EDCA is on: be|bk", bgAc);
    cmd.AddValue("backhaulRate", "scenario C wired backhaul rate", backhaulRate);
    cmd.AddValue("uplinkRate", "scenario C: rate of the shared core->server uplink (default = backhaulRate); a small value models a congested hostel uplink", uplinkRate);
    cmd.AddValue("backhaulDelayMs", "scenario C wired link delay [ms] per link (keep small: see comment on CsmaChannel)", backhaulDelayMs);
    cmd.AddValue("flowmon", "1 = also run FlowMonitor and write flows.csv", flowmon);
    cmd.AddValue("acStats", "1 = write per-node per-AC queue statistics (priority-survives-forwarding check)", acStats);
    cmd.AddValue("progress", "1 = print sim-time/event/tx-frame progress every 2 s (debug)", progress);
    cmd.AddValue("unitTest", "1 = run queue-disc unit test and exit", unit);
    cmd.Parse(argc, argv);
    if (unit) return RunUnitTest();
    NS_ABORT_MSG_IF(workload.empty(), "--workload is required");
    NS_ABORT_MSG_IF(scenario != "A" && scenario != "B" && scenario != "C", "scenario must be A|B|C");
    if (prio.empty()) prio = (scenario == "A") ? "both" : "none";
    if (scenario != "A" && prio != "none")
    {
        std::fprintf(stderr, "note: priority forced to 'none' for scenario %s\n", scenario.c_str());
        prio = "none";
    }
    NS_ABORT_MSG_IF(prio != "none" && prio != "edca" && prio != "queue" && prio != "both", "bad priorityMode");
    g_edca = (prio == "edca" || prio == "both");
    bool qprio = (prio == "queue" || prio == "both");
    g_bgBk = (bgAc == "bk");

    auto wall0 = std::chrono::steady_clock::now();
    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(run);
    uint64_t whash = LoadWorkload(workload);
    const uint32_t n = g_n;
    NS_ABORT_MSG_IF(n > 1000, "too many nodes");
    g_dead.assign(n + 1, false);
    g_rec.assign(g_sos.size(), SosRec());
    std::memset(g_origSev, 0, sizeof g_origSev);
    std::memset(g_macDrop, 0, sizeof g_macDrop);

    Config::SetDefault("ns3::UdpSocket::RcvBufSize", UintegerValue(1 << 20));

    // ---- nodes + mobility -------------------------------------------------------------------
    NodeContainer students; students.Create(n);
    NodeContainer deskNode; deskNode.Create(1);              // node id n: security desk / server
    NodeContainer apNodes;
    if (scenario == "C") apNodes.Create(nAps);
    NodeContainer allWifiNodes(students, deskNode);          // ad-hoc: all of these are radios
    for (uint32_t i = 0; i <= n; ++i)
    {
        Ptr<Node> node = (i < n) ? students.Get(i) : deskNode.Get(0);
        if (i < n && g_nodes[i].mobile)
        {
            MobilityHelper mh; mh.SetMobilityModel("ns3::WaypointMobilityModel"); mh.Install(node);
            Ptr<WaypointMobilityModel> m = node->GetObject<WaypointMobilityModel>();
            for (const Wpt& w : g_wpts[i]) m->AddWaypoint(Waypoint(Seconds(w.t), Vector(w.x, w.y, w.z)));
        }
        else
        {
            MobilityHelper mh; mh.SetMobilityModel("ns3::ConstantPositionMobilityModel"); mh.Install(node);
            node->GetObject<MobilityModel>()->SetPosition(Vector(g_nodes[i].x, g_nodes[i].y, g_nodes[i].z));
        }
    }
    for (uint32_t k = 0; k < apNodes.GetN(); ++k)   // 2 APs per floor (x=10 m and x=30 m), corridor ceiling
    {
        MobilityHelper mh; mh.SetMobilityModel("ns3::ConstantPositionMobilityModel"); mh.Install(apNodes.Get(k));
        double x = (k % 2 == 0) ? 10.0 : 30.0;
        apNodes.Get(k)->GetObject<MobilityModel>()->SetPosition(Vector(x, 7.5, (k / 2) * kFloorH + 2.5));
    }

    // ---- channel factory: LogDistance -> SvWallFloorLoss (same in every scenario) -------------
    Ptr<PropagationLossModel> lastLoss;               // top of the loss chain of the last channel made
    auto makeChannel = [&]() {
        Ptr<YansWifiChannel> ch = CreateObject<YansWifiChannel>();
        Ptr<LogDistancePropagationLossModel> ld = CreateObject<LogDistancePropagationLossModel>();
        ld->SetAttribute("Exponent", DoubleValue(ple));
        ld->SetAttribute("ReferenceLoss", DoubleValue(refLoss));
        Ptr<SvWallFloorLossModel> wf = CreateObject<SvWallFloorLossModel>();
        wf->SetAttribute("WallLoss", DoubleValue(wallDb));
        wf->SetAttribute("FloorLoss", DoubleValue(floorDb));
        ld->SetNext(wf);
        lastLoss = ld;
        ch->SetPropagationLossModel(ld);
        ch->SetPropagationDelayModel(CreateObject<ConstantSpeedPropagationDelayModel>());
        return ch;
    };

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211n);
    if (nonUniSame)
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager", "DataMode", StringValue(mcsStr),
                                     "ControlMode", StringValue("HtMcs0"), "NonUnicastMode", StringValue(mcsStr));
    else
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager", "DataMode", StringValue(mcsStr),
                                     "ControlMode", StringValue("HtMcs0"));
    auto makePhy = [&](Ptr<YansWifiChannel> ch, int chanNo) {
        YansWifiPhyHelper phy;
        phy.SetChannel(ch);
        std::ostringstream cs; cs << "{" << chanNo << ", 20, BAND_5GHZ, 0}";
        phy.Set("ChannelSettings", StringValue(cs.str()));
        phy.Set("TxPowerStart", DoubleValue(txPower));
        phy.Set("TxPowerEnd", DoubleValue(txPower));
        phy.Set("RxSensitivity", DoubleValue(rxSens));
        return phy;
    };

    // ---- traffic-control: explicit mq root, identical limits in every scenario ----------------
    auto makeTch = [&]() {
        TrafficControlHelper tch;
        uint16_t handle = tch.SetRootQueueDisc("ns3::MqQueueDisc");
        TrafficControlHelper::ClassIdList cls = tch.AddQueueDiscClasses(handle, 4, "ns3::QueueDiscClass");
        std::ostringstream lim; lim << tcQ << "p";
        for (int i = 0; i < 4; ++i)
        {
            if (qprio) tch.AddChildQueueDisc(handle, cls[i], "ns3::SvPriorityQueueDisc", "MaxSize", StringValue(lim.str()));
            else tch.AddChildQueueDisc(handle, cls[i], "ns3::FifoQueueDisc", "MaxSize", StringValue(lim.str()));
        }
        return tch;
    };

    std::vector<Ptr<WifiNetDevice>> radio(n + 1);            // wifi device of each student (+ desk in A/B)
    std::vector<Ptr<WifiNetDevice>> apRadio;
    NetDeviceContainer ipDevs;                               // devices that carry IP, ordered students..., desk
    QueueDiscContainer rootQdiscs;
    std::vector<Ptr<QueueDisc>> qdiscOf(n + 1);
    std::vector<Ptr<WifiMac>> allMacs;
    int assocCount = 0;
    InternetStackHelper stack;
    AodvHelper aodv; DsdvHelper dsdv;
    aodv.Set("EnableHello", BooleanValue(aodvHello != 0));
    aodv.Set("DestinationOnly", BooleanValue(aodvDestOnly != 0));
    aodv.Set("GratuitousReply", BooleanValue(aodvGrat != 0));

    if (scenario != "C")
    {
        // ======================= ad-hoc mesh (A / B) ==========================================
        YansWifiPhyHelper phy = makePhy(makeChannel(), 36);
        WifiMacHelper mac; mac.SetType("ns3::AdhocWifiMac");
        NetDeviceContainer devs = wifi.Install(phy, mac, allWifiNodes);
        if (routing == "dsdv") stack.SetRoutingHelper(dsdv); else stack.SetRoutingHelper(aodv);
        stack.Install(allWifiNodes);
        for (uint32_t i = 0; i <= n; ++i)
        {
            radio[i] = DynamicCast<WifiNetDevice>(devs.Get(i));
            WifiHelper::AssignStreams(NetDeviceContainer(devs.Get(i)), 1000000 + 1000 * i);
            allMacs.push_back(radio[i]->GetMac());
        }
        for (uint32_t i = 0; i <= n; ++i)
        {
            // DsdvHelper has no AssignStreams in ns-3.44: DSDV jitter streams stay auto-assigned.
            if (routing != "dsdv") aodv.AssignStreams(NodeContainer(allWifiNodes.Get(i)), 5000000 + 10 * i);
        }
        TrafficControlHelper tch = makeTch();
        rootQdiscs = tch.Install(devs);
        for (uint32_t i = 0; i <= n; ++i) qdiscOf[i] = rootQdiscs.Get(i);
        ipDevs = devs;
    }
    else
    {
        // ======================= infrastructure Wi-Fi (C) ======================================
        // Each STA is statically bound to the AP with the highest computed RSS (ns-3 has no roaming).
        Ptr<YansWifiChannel> probe = makeChannel();
        Ptr<PropagationLossModel> plm = lastLoss;
        std::vector<int> apOf(n);
        for (uint32_t i = 0; i < n; ++i)
        {
            double best = -1e9; int bk = 0;
            for (uint32_t k = 0; k < nAps; ++k)
            {
                double rss = plm->CalcRxPower(txPower, apNodes.Get(k)->GetObject<MobilityModel>(),
                                              students.Get(i)->GetObject<MobilityModel>());
                if (rss > best) { best = rss; bk = k; }
            }
            apOf[i] = bk;
        }
        const int chanNo[8] = {36, 40, 44, 48, 52, 56, 60, 64}; // 8 orthogonal 20 MHz channels, 1 per AP (optimistic for C)
        std::vector<Ptr<NetDevice>> staDev(n);
        apRadio.resize(nAps);
        std::vector<Ptr<NetDevice>> apWifiDev(nAps);
        for (uint32_t k = 0; k < nAps; ++k)
        {
            YansWifiPhyHelper phy = makePhy(makeChannel(), chanNo[k % 8]);
            Ssid ssid = Ssid("sv-ap-" + std::to_string(k));
            WifiMacHelper macAp; macAp.SetType("ns3::ApWifiMac", "Ssid", SsidValue(ssid));
            NetDeviceContainer d = wifi.Install(phy, macAp, apNodes.Get(k));
            apWifiDev[k] = d.Get(0);
            apRadio[k] = DynamicCast<WifiNetDevice>(d.Get(0));
            WifiHelper::AssignStreams(d, 2000000 + 1000 * k);
            allMacs.push_back(apRadio[k]->GetMac());
            NodeContainer mine;
            for (uint32_t i = 0; i < n; ++i) if (apOf[i] == int(k)) mine.Add(students.Get(i));
            if (mine.GetN() == 0) continue;
            WifiMacHelper macSta; macSta.SetType("ns3::StaWifiMac", "Ssid", SsidValue(ssid));
            NetDeviceContainer sd = wifi.Install(phy, macSta, mine);
            uint32_t j = 0;
            for (uint32_t i = 0; i < n; ++i)
                if (apOf[i] == int(k))
                {
                    staDev[i] = sd.Get(j++);
                    WifiHelper::AssignStreams(NetDeviceContainer(staDev[i]), 1000000 + 1000 * i);
                }
        }
        // wired backhaul: star of full-rate CSMA links  AP_k --- core switch --- campus server (= desk node).
        // (A single shared CSMA bus is NOT used: ns-3's CsmaChannel stays busy for the whole propagation
        //  delay, which would cap the wired side at 1/delay frames per second and be a modelling artefact.)
        CsmaHelper csma;
        csma.SetChannelAttribute("DataRate", DataRateValue(DataRate(backhaulRate)));
        csma.SetChannelAttribute("Delay", TimeValue(MicroSeconds(backhaulDelayMs * 1000.0)));
        NodeContainer coreNode; coreNode.Create(1);
        NetDeviceContainer corePorts;
        std::vector<Ptr<NetDevice>> apUp(nAps);
        for (uint32_t k = 0; k < nAps; ++k)
        {
            NetDeviceContainer l = csma.Install(NodeContainer(apNodes.Get(k), coreNode.Get(0)));
            apUp[k] = l.Get(0);
            corePorts.Add(l.Get(1));
        }
        CsmaHelper csmaUp = csma;
        if (!uplinkRate.empty()) csmaUp.SetChannelAttribute("DataRate", DataRateValue(DataRate(uplinkRate)));
        NetDeviceContainer srvLink = csmaUp.Install(NodeContainer(deskNode.Get(0), coreNode.Get(0)));
        corePorts.Add(srvLink.Get(1));
        BridgeHelper bridge;
        bridge.Install(coreNode.Get(0), corePorts);
        for (uint32_t k = 0; k < nAps; ++k)
            bridge.Install(apNodes.Get(k), NetDeviceContainer(apWifiDev[k], apUp[k]));
        stack.Install(students);
        stack.Install(deskNode);
        TrafficControlHelper tch = makeTch();
        for (uint32_t i = 0; i < n; ++i)
        {
            radio[i] = DynamicCast<WifiNetDevice>(staDev[i]);
            ipDevs.Add(staDev[i]);
            allMacs.push_back(radio[i]->GetMac());
            qdiscOf[i] = tch.Install(staDev[i]).Get(0);
        }
        ipDevs.Add(srvLink.Get(0));                          // server's CSMA device
    }

    // ---- MAC queue limits: identical everywhere (so expiry is never mistaken for overflow) ----
    for (uint32_t i = 0; i < radio.size(); ++i)
        if (radio[i])
            for (AcIndex ac : {AC_BE, AC_BK, AC_VI, AC_VO})
            {
                Ptr<WifiMacQueue> q = radio[i]->GetMac()->GetTxopQueue(ac);
                q->SetMaxSize(QueueSize(std::to_string(macQ) + "p"));
                q->SetMaxDelay(Seconds(macDelay));
            }
    for (auto& ap : apRadio)
        for (AcIndex ac : {AC_BE, AC_BK, AC_VI, AC_VO})
        {
            Ptr<WifiMacQueue> q = ap->GetMac()->GetTxopQueue(ac);
            q->SetMaxSize(QueueSize(std::to_string(macQ) + "p"));
            q->SetMaxDelay(Seconds(macDelay));
        }

    // ---- addressing: students 10.1.0.1.., desk next (identical in all scenarios) --------------
    Ipv4AddressHelper addr; addr.SetBase("10.1.0.0", "255.255.0.0");
    Ipv4InterfaceContainer ifc = addr.Assign(ipDevs);
    g_addr.resize(n + 1);
    for (uint32_t i = 0; i <= n; ++i) g_addr[i] = ifc.GetAddress(i);

    // ---- traces -----------------------------------------------------------------------------------
    for (uint32_t i = 0; i <= n; ++i)
    {
        if (!qdiscOf[i]) continue;
        for (int ac = 0; ac < 4; ++ac)
        {
            Ptr<QueueDisc> child = qdiscOf[i]->GetQueueDiscClass(ac)->GetQueueDisc();
            child->TraceConnectWithoutContext("DropBeforeEnqueue", MakeCallback(&OnTcDrop));
            child->TraceConnectWithoutContext("DropAfterDequeue", MakeCallback(&OnTcDrop));
        }
    }
    for (auto& m : allMacs) m->TraceConnectWithoutContext("DroppedMpdu", MakeCallback(&OnMacDrop));
    Config::ConnectWithoutContext("/NodeList/*/$ns3::Ipv4L3Protocol/SendOutgoing", MakeCallback(&OnSendOutgoing));
    Config::ConnectWithoutContext("/NodeList/*/$ns3::Ipv4L3Protocol/Drop", MakeCallback(&OnIpDrop));
    Config::ConnectWithoutContext("/NodeList/*/DeviceList/*/$ns3::WifiNetDevice/Phys/*/PhyTxBegin", MakeCallback(&OnPhyTx));

    // ---- sockets ----------------------------------------------------------------------------------
    g_tx.resize(n);
    for (uint32_t i = 0; i < n; ++i)
    {
        g_tx[i] = Socket::CreateSocket(students.Get(i), UdpSocketFactory::GetTypeId());
        g_tx[i]->Bind();
        Ptr<Socket> rx = Socket::CreateSocket(students.Get(i), UdpSocketFactory::GetTypeId());
        rx->Bind(InetSocketAddress(Ipv4Address::GetAny(), kPortBg));
        rx->SetIpRecvTtl(true);
        rx->SetRecvCallback(MakeCallback(&OnRecv));
    }
    for (uint16_t port : {uint16_t(kPortWarm), uint16_t(kPortSos + 1), uint16_t(kPortSos + 2),
                          uint16_t(kPortSos + 3), kPortBg})
    {
        Ptr<Socket> rx = Socket::CreateSocket(deskNode.Get(0), UdpSocketFactory::GetTypeId());
        rx->Bind(InetSocketAddress(Ipv4Address::GetAny(), port));
        rx->SetIpRecvTtl(true);
        rx->SetRecvCallback(MakeCallback(&OnRecv));
    }

    // ---- schedule workload ---------------------------------------------------------------------------
    for (auto& w : g_warmPings) Simulator::Schedule(Seconds(w.first), &DoWarm, w.second);
    for (uint32_t i = 0; i < g_sos.size(); ++i) Simulator::Schedule(Seconds(g_sos[i].t), &DoSos, i);
    for (uint32_t i = 0; i < g_bg.size(); ++i) Simulator::Schedule(Seconds(g_bg[i].t0), &BgTick, i, 0u);

    // convergence / association checks exactly when measurement starts
    Simulator::Schedule(Seconds(g_warm), [&]() {
        g_warmReachedAtWarm = g_warmReached.size();
        if (scenario == "C")
            for (uint32_t i = 0; i < n; ++i)
            {
                Ptr<StaWifiMac> sm = DynamicCast<StaWifiMac>(radio[i]->GetMac());
                if (sm && sm->IsAssociated()) ++g_assocAtWarm;
            }
    });

    // failures
    if (failure == "node")
    {
        NS_ABORT_MSG_IF(scenario == "C", "failure=node is for the ad-hoc scenarios");
        Simulator::Schedule(Seconds(failTime), [&]() {
            for (uint32_t id : g_failNodes)
            {
                g_dead[id] = true;
                Ptr<Ipv4> ip4 = students.Get(id)->GetObject<Ipv4>();
                ip4->SetDown(1);
                radio[id]->GetPhy()->SetOffMode();
            }
        });
    }
    else if (failure == "ap")
    {
        NS_ABORT_MSG_IF(scenario != "C", "failure=ap is for scenario C");
        Simulator::Schedule(Seconds(failTime), [&]() { apRadio[g_failAp]->GetPhy()->SetOffMode(); });
    }

    if (progress && scenario == "C")
    {
        for (double t : {1.0, 2.0, 4.0, 8.0, 10.0, 20.0, 40.0})
            if (t < g_end)
                Simulator::Schedule(Seconds(t), [t, &radio, n]() {
                    uint32_t a = 0; std::string un;
                    for (uint32_t i = 0; i < n; ++i)
                    {
                        Ptr<StaWifiMac> sm = DynamicCast<StaWifiMac>(radio[i]->GetMac());
                        if (sm && sm->IsAssociated()) ++a; else un += " " + std::to_string(i);
                    }
                    std::fprintf(stderr, "[assoc] t=%.0f associated=%u unassociated:%s\n", t, a, un.c_str());
                });
    }
    if (progress)
    {
        for (double t = 2; t < g_end; t += 2)
            Simulator::Schedule(Seconds(t), [t]() {
                std::fprintf(stderr, "[progress] t=%.0f events=%llu phyTx=%llu\n", t,
                             (unsigned long long)Simulator::GetEventCount(), (unsigned long long)g_phyTx);
            });
    }
    FlowMonitorHelper fmh;
    Ptr<FlowMonitor> fm;
    if (flowmon) fm = fmh.InstallAll();

    Simulator::Stop(Seconds(g_end));
    Simulator::Run();
    uint64_t events = Simulator::GetEventCount();
    if (progress)
    {
        std::fprintf(stderr, "[debug] warm-ping sources NOT reached by end:");
        for (uint32_t i = 0; i < n; ++i) if (!g_warmReached.count(i)) std::fprintf(stderr, " %u", i);
        std::fprintf(stderr, "\n");
    }

    // ======================= results ==================================================================
    std::string cmdline;
    for (int i = 0; i < argc; ++i) cmdline += std::string(i ? " " : "") + argv[i];
    uint64_t sosTx[4] = {0, 0, 0, 0}, sosRx[4] = {0, 0, 0, 0};
    {
        std::ofstream pk(outDir + "/packets.csv");
        pk << "id,src,sev,burst,valid,tx_s,rx_s,hops\n";
        for (uint32_t i = 0; i < g_sos.size(); ++i)
        {
            const SosRec& r = g_rec[i];
            pk << i << "," << g_sos[i].src << "," << g_sos[i].sev << "," << g_sos[i].burst << ","
               << (r.valid ? 1 : 0) << "," << (r.valid ? r.tx : g_sos[i].t) << "," << r.rx << "," << r.hops << "\n";
            if (r.valid) { sosTx[g_sos[i].sev]++; if (r.rx >= 0) sosRx[g_sos[i].sev]++; }
        }
    }
    uint64_t tcOver = 0, tcPush = 0, tcOverSos = 0, tcPushSos = 0, tcOther = 0;
    for (auto& kv : g_tcDrop)
    {
        bool push = kv.first.find("Push-out") != std::string::npos;
        bool over = kv.first.find("limit exceeded") != std::string::npos;
        uint64_t sosPart = g_tcDropSos.count(kv.first) ? g_tcDropSos[kv.first] : 0;
        if (push) { tcPush += kv.second; tcPushSos += sosPart; }
        else if (over) { tcOver += kv.second; tcOverSos += sosPart; }
        else tcOther += kv.second;
    }
    auto macSum = [&](int r, int lo, int hi) { uint64_t s = 0; for (int b = lo; b <= hi; ++b) s += g_macDrop[r][b]; return s; };
    double wallclock = std::chrono::duration<double>(std::chrono::steady_clock::now() - wall0).count();
    {
        std::ofstream sm(outDir + "/summary.csv");
        sm << "scenario,priorityMode,routing,run,nNodes,workload,workloadHash,failure,failTime,txPower,mcs,"
              "pathLossExp,refLoss,wallLoss,floorLoss,tcQueue,macQueue,macDelay,bgAc,duration,warmup,"
              "sosTx1,sosTx2,sosTx3,sosRx1,sosRx2,sosRx3,bgTxBytes,bgRxBytes,sosRxBytes,"
              "warmReached,assocAtWarm,ctrlPkts,ctrlBytes,"
              "tcOverflow,tcPushout,tcOverflowSos,tcPushoutSos,tcOther,"
              "macQueueFull,macExpired,macRetryLimit,macOld,macQueueFullSos,macExpiredSos,macRetryLimitSos,"
              "noRoute,ipDropOther,events,wallclock_s\n";
        sm << scenario << "," << prio << "," << routing << "," << run << "," << n << "," << workload << ","
           << Hex16(whash) << "," << failure << "," << failTime << "," << txPower << "," << mcsStr << ","
           << ple << "," << refLoss << "," << wallDb << "," << floorDb << "," << tcQ << "," << macQ << ","
           << macDelay << "," << bgAc << "," << (g_end - g_warm) << "," << g_warm << ","
           << sosTx[1] << "," << sosTx[2] << "," << sosTx[3] << "," << sosRx[1] << "," << sosRx[2] << "," << sosRx[3] << ","
           << g_bgTxBytes << "," << g_bgRxBytes << "," << g_sosRxBytes << ","
           << g_warmReachedAtWarm << "," << g_assocAtWarm << "," << g_ctrlPkts << "," << g_ctrlBytes << ","
           << tcOver << "," << tcPush << "," << tcOverSos << "," << tcPushSos << "," << tcOther << ","
           << macSum(0, 0, 4) << "," << macSum(1, 0, 4) << "," << macSum(2, 0, 4) << "," << macSum(3, 0, 4) << ","
           << macSum(0, 1, 3) << "," << macSum(1, 1, 3) << "," << macSum(2, 1, 3) << ","
           << g_noRoute << "," << g_ipDropOther << "," << events << "," << wallclock << "\n";
    }
    if (acStats)
    {
        // per node, per access category: packets enqueued, minus those the node originated itself
        // (-> packets it RELAYED in that AC).  Proves priority marking survives forwarding.
        std::ofstream as(outDir + "/ac_stats.csv");
        as << "node,ac,enqueued,originated_sev,relayed\n";
        uint64_t relayVo = 0, relayVi = 0, origVo = 0, origVi = 0;
        for (uint32_t i = 0; i <= n; ++i)
        {
            if (!qdiscOf[i]) continue;
            for (int ac = 0; ac < 4; ++ac)
            {
                Ptr<QueueDisc> child = qdiscOf[i]->GetQueueDiscClass(ac)->GetQueueDisc();
                uint64_t enq = child->GetStats().nTotalEnqueuedPackets;
                // queue index of AC: 0=BE 1=BK 2=VI 3=VO (ns-3 txq order follows AcIndex)
                uint64_t orig = 0;
                if (i < n && g_edca) { if (ac == 3) orig = g_origSev[3][i]; if (ac == 2) orig = g_origSev[2][i]; }
                uint64_t relayed = enq > orig ? enq - orig : 0;
                as << i << "," << ac << "," << enq << "," << orig << "," << relayed << "\n";
                if (ac == 3) { relayVo += relayed; origVo += orig; }
                if (ac == 2) { relayVi += relayed; origVi += orig; }
            }
        }
        std::printf("AC-STATS VO originated=%llu relayed=%llu | VI originated=%llu relayed=%llu\n",
                    (unsigned long long)origVo, (unsigned long long)relayVo,
                    (unsigned long long)origVi, (unsigned long long)relayVi);
    }
    if (flowmon)
    {
        fm->CheckForLostPackets();
        Ptr<Ipv4FlowClassifier> cl = DynamicCast<Ipv4FlowClassifier>(fmh.GetClassifier());
        std::ofstream ff(outDir + "/flows.csv");
        ff << "flow,src,dst,dport,txPackets,rxPackets,txBytes,rxBytes,delaySum_s,lostPackets\n";
        for (auto& kv : fm->GetFlowStats())
        {
            Ipv4FlowClassifier::FiveTuple t = cl->FindFlow(kv.first);
            ff << kv.first << "," << t.sourceAddress << "," << t.destinationAddress << "," << t.destinationPort << ","
               << kv.second.txPackets << "," << kv.second.rxPackets << "," << kv.second.txBytes << ","
               << kv.second.rxBytes << "," << kv.second.delaySum.GetSeconds() << "," << kv.second.lostPackets << "\n";
        }
    }
    if (progress)
        std::fprintf(stderr, "[debug] AODV ctrl in window: RREQ=%llu RREP=%llu RERR=%llu HELLO=%llu\n",
                     (unsigned long long)g_aodvType[1], (unsigned long long)g_aodvType[2],
                     (unsigned long long)g_aodvType[3], (unsigned long long)g_aodvType[5]);
    std::printf("DONE scenario=%s prio=%s n=%u run=%u hash=%s events=%llu wall=%.1fs | SOS pdr: s3 %llu/%llu s2 %llu/%llu s1 %llu/%llu | warm %u/%u assoc %u\n",
                scenario.c_str(), prio.c_str(), n, run, Hex16(whash).c_str(), (unsigned long long)events, wallclock,
                (unsigned long long)sosRx[3], (unsigned long long)sosTx[3], (unsigned long long)sosRx[2],
                (unsigned long long)sosTx[2], (unsigned long long)sosRx[1], (unsigned long long)sosTx[1],
                g_warmReachedAtWarm, n, g_assocAtWarm);
    Simulator::Destroy();
    return 0;
}
