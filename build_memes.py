#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 memes.json —— 瑞斯图尔法庭词库
语料来源：3 路并行检索共 387 条原始语料（贴吧/NGA/B站/17173/BUFF163/知乎/dota2.com.cn）
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

# ══════════════════════════════════════════════════════════
# METRICS 指标白名单
# ══════════════════════════════════════════════════════════
METRICS = {
    "lh_at_10": {"label":"10分钟正补","source":"OpenDota lh_t[10]","type":"int","verified":True,
                 "note":"需 parse；未解析为 null。基准 核心40+ 辅助10+"},
    "dn_at_10": {"label":"10分钟反补","source":"OpenDota dn_t[10]","type":"int","verified":True,"note":"需 parse"},
    "xp_at_10": {"label":"10分钟经验","source":"OpenDota xp_t[10]","type":"int","verified":True,"note":"需 parse"},
    "lane_role": {"label":"分路","source":"OpenDota lane_role","type":"int","verified":True,
                  "note":"1优势 2中 3劣势 4野区；需 parse"},
    "gpm": {"label":"每分钟金钱","source":"OpenDota gold_per_min","type":"int","verified":True,"note":"核心基准500+"},
    "xpm": {"label":"每分钟经验","source":"OpenDota xp_per_min","type":"int","verified":True,"note":""},
    "net_worth": {"label":"总净值","source":"OpenDota net_worth","type":"int","verified":True,"note":""},
    "gold_share": {"label":"经济占比","source":"net_worth/队伍总net_worth","type":"pct","verified":True,"note":"计算字段，均值20%"},
    "last_hits": {"label":"总正补","source":"OpenDota last_hits","type":"int","verified":True,"note":""},
    "hero_damage": {"label":"英雄伤害","source":"OpenDota hero_damage","type":"int","verified":True,"note":""},
    "damage_share": {"label":"伤害占比","source":"hero_damage/队伍总hero_damage","type":"pct","verified":True,"note":"计算字段，均值20%"},
    "tower_damage": {"label":"塔伤","source":"OpenDota tower_damage","type":"int","verified":True,"note":""},
    "hero_healing": {"label":"治疗量","source":"OpenDota hero_healing","type":"int","verified":True,"note":""},
    "kills": {"label":"击杀","source":"OpenDota kills","type":"int","verified":True,"note":""},
    "deaths": {"label":"死亡","source":"OpenDota deaths","type":"int","verified":True,"note":""},
    "assists": {"label":"助攻","source":"OpenDota assists","type":"int","verified":True,"note":""},
    "kda_ratio": {"label":"KDA","source":"(kills+assists)/max(deaths,1)","type":"float","verified":True,"note":"计算字段"},
    "teamfight_participation": {"label":"参团率","source":"OpenDota teamfight_participation","type":"pct",
                                "verified":True,"note":"需 parse；均值约65%"},
    "stuns": {"label":"控制时长","source":"OpenDota stuns","type":"float","verified":True,"note":"秒；需 parse"},
    "obs_placed": {"label":"假眼数","source":"OpenDota obs_placed","type":"int","verified":True,"note":"需 parse；辅助基准10+"},
    "sen_placed": {"label":"真眼数","source":"OpenDota sen_placed","type":"int","verified":True,"note":"需 parse"},
    "observer_kills": {"label":"排眼数","source":"OpenDota observer_kills","type":"int","verified":True,"note":""},
    "tp_uses": {"label":"TP使用次数","source":"OpenDota item_uses.tpscroll","type":"int","verified":True,
                "note":"⚠禁止用 purchase_tpscroll —— 那是购买记录，null≠0。教训见 Lina 事件"},
    "buyback_count": {"label":"买活次数","source":"OpenDota buyback_count","type":"int","verified":True,"note":""},
    "camps_stacked": {"label":"堆野数","source":"OpenDota camps_stacked","type":"int","verified":True,"note":""},
    "roshan_kills": {"label":"肉山击杀","source":"OpenDota roshan_kills","type":"int","verified":True,"note":""},
    "duration": {"label":"时长","source":"OpenDota duration","type":"int","verified":True,"note":"秒"},
    "radiant_win": {"label":"天辉胜","source":"OpenDota radiant_win","type":"bool","verified":True,"note":""},
    "is_parsed": {"label":"已解析","source":"OpenDota version!=null","type":"bool","verified":True,
                  "note":"false 时所有 parse-only 指标不可用"},
}

E = []
def add(id, cat, sev, tone, trig, req, text, var=None, forb=None, src=None):
    e = {"id":id, "category":cat, "severity":sev, "tone":tone}
    if trig: e["trigger"] = trig
    e["requires"] = req
    if forb: e["forbidden_context"] = forb
    e["text"] = text
    if var: e["variants"] = var
    e["source"] = src or {}
    E.append(e)

C = lambda m,o,v: {"metric":m,"op":o,"value":v}
S = lambda o,raw,note="": {"origin":o,"raw":raw,"note":note}

# ══════════════ LANE 对线 ══════════════
add("lane_lh_dire_core","lane",3,["roast","meme"],
  {"all":[C("lh_at_10","<=",12),C("role","in",["core","mid","carry"])]},["lh_at_10"],
  {"tag":"对线崩盘","fact":"10 分钟正补 {lh_at_10}，核心位基准 40+。",
   "quip":"你这补刀跟没有一样。","verdict":"本庭认定：对线期已提前下班。",
   "share":"十分钟 {lh_at_10} 刀","safe":"对线期补刀明显偏低，建议复盘补刀节奏与站位。"},
  [{"quip":"十分钟几个刀？问完我自己闭麦。"},
   {"quip":"兵是你的仇人吗，一个都不碰。"},
   {"quip":"这个游戏叫抗压。抗到 {lh_at_10} 个刀。"}],
  ["data_incomplete","victory"],
  S("游戏内常见/贴吧","你这补刀跟没有一样 / 十分钟几个刀？","YYF「这个游戏叫抗压」= 对线被压的美化说法"))

add("lane_lh_low_core","lane",2,["roast"],
  {"all":[C("lh_at_10","<=",25),C("lh_at_10",">",12),C("role","in",["core","mid","carry"])]},["lh_at_10"],
  {"tag":"对线失守","fact":"10 分钟正补 {lh_at_10}，低于核心位基准。",
   "quip":"这个游戏叫抗压，对吧。","verdict":"本庭认定：对线期处于劣势。",
   "safe":"对线期资源获取偏低，可复盘补刀与换血节奏。"},
  [{"quip":"讲道理，对面这个组合是有点脏。"},{"quip":"抗压位，被压是正常的，压死了也别怪我。"}],
  ["data_incomplete"],S("BUFF163/YYF语录","这个游戏叫抗压"))

add("lane_dn_zero","lane",1,["roast","meme"],
  {"all":[C("dn_at_10","<=",1),C("role","in",["core","mid"])]},["dn_at_10"],
  {"tag":"不会反补","fact":"10 分钟反补 {dn_at_10}。",
   "quip":"反补键是坏了还是没绑？","safe":"反补数偏低，控线能力有提升空间。"},
  None,["data_incomplete"],S("17173术语表","反补 / 正反补 / 控线"))

add("lane_xp_starved","lane",2,["roast"],
  {"all":[C("xp_at_10","<=",1800),C("role","in",["core","mid","carry","offlane"])]},["xp_at_10"],
  {"tag":"经验真空","fact":"10 分钟经验 {xp_at_10}，等级落后。",
   "quip":"你是去旅游的吧，线都不站。","verdict":"本庭认定：对线期严重脱节。",
   "safe":"对线期经验获取不足，建议减少无效游走。"},
  [{"quip":"假 4 号位实锤，树后吸经验都吸不到。"}],["data_incomplete"],
  S("r/DotA2中文","假 4 号位：不插眼不扫眼不拉野，只躲树后吸经验",
    "⚠限核心/劣单：辅助10分钟经验本就偏低，不构成失职"))

add("lane_offlane_collapse","lane",3,["roast","meme"],
  {"all":[C("lh_at_10","<=",8),C("role","in",["offlane"])]},["lh_at_10"],
  {"tag":"劣单崩盘","fact":"10 分钟正补 {lh_at_10}，劣单基准 20+。",
   "quip":"抗压位，抗到一个刀都没有。","verdict":"本庭认定：劣势路彻底失守。",
   "share":"劣单十分钟 {lh_at_10} 刀","safe":"劣势路补刀严重偏低，建议复盘换血与拉野时机。"},
  [{"quip":"别送，别送，千万别送——刀也别补了是吧。"},
   {"quip":"三号位是最容易被千夫所指的背锅位。今天实至名归。"}],
  ["data_incomplete","victory"],
  S("已验证:17173甩锅指南/知乎","抗压位 / 别送别送千万别送 / 三号位是最容易被千夫所指的背锅位"))

add("lane_solid","lane",0,["fact","praise"],
  {"all":[C("lh_at_10",">=",45),C("role","in",["core","mid","carry"])]},["lh_at_10"],
  {"tag":"对线稳健","fact":"10 分钟正补 {lh_at_10}，对线期建立优势。",
   "quip":"这个游戏叫我很肥好吧，不多说。","safe":"对线期发育良好。"},
  None,None,S("YYF语录","这个游戏叫我很肥好吧，不多说"))

# ══════════════ FARM 经济 ══════════════
add("farm_gold_sink","farm",3,["roast","meme"],
  {"all":[C("gold_share",">=",0.26),C("damage_share","<=",0.14)]},["gold_share","damage_share"],
  {"tag":"经济黑洞","fact":"吃掉全队 {gold_share} 经济，只打出 {damage_share} 伤害。",
   "quip":"钱都进你兜里了，输出呢？","verdict":"本庭认定：资源投入产出严重失衡。",
   "share":"{gold_share} 经济换 {damage_share} 输出",
   "safe":"经济占比高但伤害转化偏低，建议复盘参战时机与出装。"},
  [{"quip":"你们懂什么，这叫核威慑。"},
   {"quip":"你玩个大哥就刷钱，抓人要队友抓，救人要队友救，要你干嘛呢？"},
   {"quip":"无解肥，肥到一点用没有。"}],
  ["data_incomplete","victory"],
  S("已验证:B站查理斯语录/IGXE","你玩个大哥就刷钱…要你干嘛呢 / 核威慑",
    "「核威慑」原指BurNIng装备领先但团战没输出"))

add("farm_gpm_low_core","farm",2,["roast"],
  {"all":[C("gpm","<=",330),C("role","in",["core","carry"])]},["gpm"],
  {"tag":"发育停滞","fact":"GPM {gpm}，核心位基准 500+。",
   "quip":"你为什么不去打钱？","verdict":"本庭认定：未能履行核心位发育职责。",
   "safe":"经济效率偏低，建议优化刷钱路线与线野结合。"},
  [{"quip":"我差 100 块钱大件啊——你差的是一整局。"},
   {"quip":"这波我差 2000 就有跳刀了，很急很关键。"}],
  ["data_incomplete"],
  S("游戏内高频/OB语录","你为什么不去打钱 / 这波我差2000就有跳刀了，很急很关键"))

add("farm_afk_jungle","farm",3,["roast","meme"],
  {"all":[C("teamfight_participation","<=",0.45),C("gpm",">=",450)]},
  ["teamfight_participation","gpm"],
  {"tag":"只打钱不打架","fact":"参团率 {teamfight_participation}，GPM {gpm}。",
   "quip":"一整局都在打野，从不参战。","verdict":"本庭认定：全程脱离团队作战。",
   "share":"参团 {teamfight_participation}",
   "safe":"参团率偏低但发育良好，建议提升团队参与度。"},
  [{"quip":"你带线带哪去了？"},{"quip":"不要放海涛打钱！"}],
  ["data_incomplete","victory"],
  S("B站视频标题/海涛语录","一整局都在打野从不参战 / 不要放海涛打钱"))

add("farm_rich_dead","farm",2,["roast","meme"],
  {"all":[C("net_worth",">=",18000),C("deaths",">=",8)]},["net_worth","deaths"],
  {"tag":"肥而不强","fact":"净值 {net_worth}，死亡 {deaths} 次。",
   "quip":"装备是挺好，就是人一直躺着。","verdict":"本庭认定：装备优势未转化为战场存活。",
   "safe":"经济领先但死亡偏多，建议注意站位与开团时机。"},
  [{"quip":"我 3800 了——然后就没有然后了。"}],["data_incomplete"],
  S("幽鬼玩家梗","我 3800 了","宣告辉耀成型，泛指「我这波起来了」"))

add("farm_no_items","farm",1,["roast"],
  {"all":[C("gpm","<=",250)]},["gpm"],
  {"tag":"裸奔","fact":"GPM {gpm}，全场未成型。",
   "quip":"我裸奔，我没装备，打不动。","safe":"经济严重落后，建议复盘死亡与资源分配。"},
  None,["data_incomplete"],S("游戏内常见","我裸奔 / 我没装备"))

# ══════════════ DEATH 死亡 ══════════════
add("death_feeder","death",3,["roast","meme"],
  {"all":[C("deaths",">=",11)]},["deaths"],
  {"tag":"送葬专业户","fact":"全场死亡 {deaths} 次。",
   "quip":"送！送！送！会不会玩！","verdict":"本庭认定：反复阵亡，严重助长敌方经济。",
   "share":"死亡 {deaths} 次","safe":"死亡次数偏高，建议复盘走位与撤退时机。"},
  [{"quip":"又被抓了？你身上装 GPS 了吧。"},
   {"quip":"我再也不会死一次——然后马上又死。"},
   {"quip":"你走位很风骚啊。"}],
  ["data_incomplete","victory"],
  S("已验证:17173/怒吼天尊XB赛场原声","送！送！送！会不会玩！ / 我再也不会死一次",
    "XB原声是最著名的中文Dota嘲讽"))

add("death_high","death",2,["roast"],
  {"all":[C("deaths",">=",8),C("deaths","<",11)]},["deaths"],
  {"tag":"死亡偏多","fact":"全场死亡 {deaths} 次。",
   "quip":"别送了。虽然说的时候已经晚了。","verdict":"本庭认定：阵亡次数超出合理范围。",
   "safe":"死亡次数偏多，建议注意视野与撤退判断。"},
  [{"quip":"这么劣势你还出去打？"},{"quip":"别追了——你追它干嘛。"}],
  ["data_incomplete"],S("游戏内最高频","别送了 / 别追了 / 你追它干嘛"))

add("death_kda_bottom","death",3,["roast"],
  {"all":[C("kda_ratio","<=",1.0),C("kda_ratio","rank_is","last")]},["kda_ratio","kills","deaths","assists"],
  {"tag":"三红变态辣","fact":"KDA {kills}/{deaths}/{assists}，队内垫底。",
   "quip":"三红变态辣，下饭。","verdict":"本庭认定：本局综合表现队内最末。",
   "share":"{kills}/{deaths}/{assists}","safe":"本局数据队内偏低，建议整体复盘。"},
  [{"quip":"太辣了，就着这操作下饭。"},{"quip":"我 KDA 你们看一眼——最好别看。"}],
  ["data_incomplete","victory"],
  S("已验证:dota2.com.cn术语文/B站弹幕","三红变态辣 / 下饭 / 太辣了",
    "三红=KDA/补刀/经济三项全部低于平均"))

add("death_buyback_waste","death",2,["roast"],
  {"all":[C("buyback_count",">=",2),C("deaths",">=",8)]},["buyback_count","deaths"],
  {"tag":"买活白给","fact":"买活 {buyback_count} 次，仍死亡 {deaths} 次。",
   "quip":"买活是为了守家，不是为了再送一次。",
   "verdict":"本庭认定：买活资源使用效率低下。",
   "safe":"买活后再次阵亡，建议复盘买活时机。"},
  None,["data_incomplete"],S("游戏内常见","你买活呢？买活啊！"))

add("death_no_buyback","death",2,["roast"],
  {"all":[C("buyback_count","==",0),C("net_worth",">=",15000),C("deaths",">=",6)]},
  ["buyback_count","net_worth","deaths"],
  {"tag":"有钱不买活","fact":"净值 {net_worth}，全场买活 {buyback_count} 次。",
   "quip":"你买活呢？买活啊！","verdict":"本庭认定：关键时刻未使用买活。",
   "safe":"经济充足但未使用买活，建议复盘守家决策。"},
  None,["data_incomplete"],S("游戏内常见","你买活呢？买活啊！"))

# ══════════════ TEAMFIGHT 团战 ══════════════
add("tf_ghost","teamfight",3,["roast","meme"],
  {"all":[C("teamfight_participation","<=",0.40)]},["teamfight_participation"],
  {"tag":"团战绝缘体","fact":"参团率 {teamfight_participation}，队内最低。",
   "quip":"你人呢？团都打完了你才来。","verdict":"本庭认定：全程缺席团队作战。",
   "share":"参团率 {teamfight_participation}","safe":"参团率偏低，建议提高团队协同。"},
  [{"quip":"我去肉山做了个视野啊——做了一整局。"},
   {"quip":"刚刚下路线到家了啊。这借口用了几年了。"},
   {"quip":"团都打完了你才来，收尸型参团。"}],
  ["data_incomplete","victory"],
  S("已验证:17173甩锅指南/游戏内高频","你人呢 / 团都打完了你才来 / 我去肉山做了个视野啊",
    "「团战不在的三大标准借口」之一"))

add("tf_low_participation","teamfight",2,["roast"],
  {"all":[C("teamfight_participation","<=",0.55),C("teamfight_participation",">",0.40)]},
  ["teamfight_participation"],
  {"tag":"参团不足","fact":"参团率 {teamfight_participation}，低于均值 65%。",
   "quip":"接团啊，人呢。","verdict":"本庭认定：团队参与度不足。",
   "safe":"参团率低于均值，建议关注团战信号。"},
  [{"quip":"我技能全交了——交给野怪了。"}],["data_incomplete"],
  S("游戏内常见","接团啊 / 你人呢"))

add("tf_no_stun","teamfight",2,["roast"],
  {"all":[C("stuns","<=",5),C("role","in",["support","offlane"])]},["stuns"],
  {"tag":"控制缺席","fact":"全场控制时长 {stuns} 秒。",
   "quip":"大招呢？控制链断了都不知道谁的锅。",
   "verdict":"本庭认定：未提供有效控制贡献。",
   "safe":"控制时长偏低，建议复盘技能释放时机。"},
  [{"quip":"空大了。世纪空大。"},{"quip":"空大了甩什么锅啊，跟我们一起：R!O!T!K!"}],
  ["data_incomplete"],
  S("已验证:17173甩锅指南","空大了 / 空大了甩什么锅啊，跟我们一起ROTK",
    "「若风一指」也指空大，源自若风莱恩大招放小兵身上"))

add("tf_damage_absent","teamfight",3,["roast","meme"],
  {"all":[C("damage_share","<=",0.11),C("role","in",["core","carry","mid"])]},["damage_share"],
  {"tag":"输出绝缘","fact":"全场伤害占比 {damage_share}，核心位垫底。",
   "quip":"你这输出，是来观光的吧。","verdict":"本庭认定：核心位未承担输出职责。",
   "share":"伤害占比 {damage_share}","safe":"伤害占比偏低，建议复盘输出环境与站位。"},
  [{"quip":"核威慑。懂的都懂。"},{"quip":"站位太靠前了？你根本没站进去。"}],
  ["data_incomplete","victory"],S("IGXE/复盘用语","核威慑 / 站位太靠前了"))

add("tf_frontline_death","teamfight",2,["roast"],
  {"all":[C("deaths",">=",7),C("role","in",["carry","core"])]},["deaths"],
  {"tag":"站位灾难","fact":"后排核心死亡 {deaths} 次。",
   "quip":"你站前面干嘛？","verdict":"本庭认定：站位失当导致反复阵亡。",
   "safe":"死亡偏多，建议复盘团战站位与切入时机。"},
  [{"quip":"有 BKB 没开。经典。"},{"quip":"BKB 呢？被控死才想起来。"}],
  ["data_incomplete"],S("术语站/游戏内","站位太靠前了 / 你站前面干嘛 / 有BKB没开"))

# ══════════════ VISION 视野 ══════════════
add("vision_no_ward","vision",3,["roast","meme"],
  {"all":[C("obs_placed","<=",4),C("role","in",["support","hard_support"])]},["obs_placed"],
  {"tag":"视野真空","fact":"全场假眼 {obs_placed} 个，辅助基准 10+。",
   "quip":"眼呢？插眼了吗？","verdict":"本庭认定：未履行视野职责。",
   "share":"全场 {obs_placed} 眼","safe":"视野布置偏少，建议增加关键眼位。"},
  [{"quip":"没眼怎么打，全图黑着呢。"},
   {"quip":"假 4 号位实锤：不插眼不扫眼不拉野。"},
   {"quip":"包鸡包眼，你一个都没包。"}],
  ["data_incomplete","victory"],
  S("已验证:游戏内最高频/17173","眼呢 / 插眼了吗 / 视野呢 / 包鸡包眼",
    "「包鸡包眼」是17173 support词条原文"))

add("vision_no_sentry","vision",2,["roast"],
  {"all":[C("sen_placed","<=",2),C("role","in",["support","hard_support"])]},["sen_placed"],
  {"tag":"不排眼","fact":"全场真眼 {sen_placed} 个。",
   "quip":"排一下这个眼，说了一整局。",
   "verdict":"本庭认定：未进行有效反视野。","safe":"真眼数偏少，建议加强反视野。"},
  None,["data_incomplete"],S("游戏内/眼位攻略","排眼 / 反眼 / 排一下这个眼"))

add("vision_ward_only","vision",2,["roast","meme"],
  {"all":[C("obs_placed",">=",10),C("damage_share","<=",0.13)]},["obs_placed","damage_share"],
  {"tag":"插眼冠军","fact":"假眼 {obs_placed} 个全队第一，伤害占比 {damage_share} 垫底。",
   "quip":"插眼冠军，输出绝缘。","verdict":"本庭认定：视野贡献充分，战斗贡献不足。",
   "share":"{obs_placed} 眼 · {damage_share} 输出",
   "safe":"视野贡献突出，但战斗参与可以更积极。"},
  [{"quip":"辅助插眼放个技能就可以死了——你连技能都省了。"}],
  ["data_incomplete"],
  S("搜狐眼位攻略反讽句","辅助插眼放个技能就可以死了","讽刺「辅助无用论」的经典反讽"))

add("vision_good","vision",0,["fact","praise"],
  {"all":[C("obs_placed",">=",14)]},["obs_placed"],
  {"tag":"视野到位","fact":"全场假眼 {obs_placed} 个，视野工作扎实。",
   "quip":"这眼位，挑不出毛病。","safe":"视野贡献良好。"},
  None,None,S("社区共识","包鸡包眼"))

# ══════════════ TP / 支援 ══════════════
add("tp_never","tp_rotation",3,["roast","meme"],
  {"all":[C("tp_uses","<=",3)]},["tp_uses"],
  {"tag":"从不支援","fact":"全场 TP 使用 {tp_uses} 次，队内最少。",
   "quip":"TP 是买来看的吗？","verdict":"本庭认定：全程未提供转线支援。",
   "share":"全场 {tp_uses} 个 TP","safe":"TP 使用偏少，建议加强转线支援。"},
  [{"quip":"卧槽！谁刚才动了鸟没说啊！我 TP 没运到！"},
   {"quip":"别的路打起来了，你在原地思考人生。"}],
  ["data_incomplete","victory"],
  S("已验证:17173甩锅指南","谁动鸡了！我TP没运到！",
    "⚠必须用 item_uses.tpscroll，禁止 purchase_tpscroll —— Lina事件教训"))

add("tp_diligent","tp_rotation",0,["fact","praise"],
  {"all":[C("tp_uses",">=",12)]},["tp_uses"],
  {"tag":"支援勤快","fact":"全场 TP 使用 {tp_uses} 次，队内最多。",
   "quip":"哪都有你，这个支援意识没话说。","safe":"转线支援积极。"},
  None,None,S("产品自拟","",  "用于平衡：不能只有负面词条"))

# ══════════════ SUPPORT 辅助 ══════════════
add("support_no_contribution","support",3,["roast"],
  {"all":[C("assists","<=",6),C("obs_placed","<=",5),C("role","in",["support","hard_support"])]},
  ["assists","obs_placed"],
  {"tag":"辅助失职","fact":"助攻 {assists}，假眼 {obs_placed}。",
   "quip":"辅助干什么呢？为什么不保我？","verdict":"本庭认定：未履行辅助基本职责。",
   "safe":"辅助各项贡献偏低，建议复盘游走与视野节奏。"},
  [{"quip":"保人啊，保一下——保了个寂寞。"},{"quip":"三分钟买鸟都没做到。"}],
  ["data_incomplete","victory"],
  S("已验证:17173甩锅指南","辅助干什么呢！为什么不保我！ / 三分钟买鸟"))

add("support_no_stack","support",1,["roast"],
  {"all":[C("camps_stacked","<=",1),C("role","in",["support","hard_support"])]},["camps_stacked"],
  {"tag":"不堆野","fact":"全场堆野 {camps_stacked} 次。",
   "quip":"野都不堆，大哥吃什么？","safe":"堆野次数偏少，可提升团队资源效率。"},
  None,["data_incomplete"],S("术语站","屯野 / 拉野"))

add("support_carry_hard","support",0,["praise","fact"],
  {"all":[C("assists",">=",18),C("role","in",["support","hard_support"])]},["assists"],
  {"tag":"辅助优秀","fact":"助攻 {assists} 次，全程在场。",
   "quip":"这辅助，谁不想要。","safe":"辅助贡献突出。"},
  None,["defeat"],S("产品自拟",""))

# ══════════════ OBJECTIVE 资源 ══════════════
add("obj_no_tower","objective",2,["roast"],
  {"all":[C("tower_damage","<=",600),C("role","in",["core","carry"])]},["tower_damage"],
  {"tag":"不推塔","fact":"全场塔伤 {tower_damage}。",
   "quip":"人头打得挺欢，塔一个没碰。","verdict":"本庭认定：未转化优势为地图资源。",
   "safe":"推塔贡献偏低，建议把击杀转化为推进。"},
  [{"quip":"一波带走呢？带走个寂寞。"},{"quip":"上高啊——上不了高。"}],
  ["data_incomplete"],S("知乎/游戏内","上高啊 / 上不了高 / 一波带走"))

add("obj_team_no_push","objective",2,["roast","meme"],
  {"all":[C("duration",">=",2400),C("tower_damage","rank_is","last")]},["duration","tower_damage"],
  {"tag":"打不出节奏","fact":"比赛 {duration}，塔伤队内垫底。",
   "quip":"盾没了才上高，经典失误。","verdict":"本庭认定：未把握推进窗口。",
   "safe":"推进节奏偏慢，建议复盘优势期决策。"},
  [{"quip":"3154。懂的都懂。"},{"quip":"高地不好上——所以就不上了？"}],
  ["data_incomplete"],
  S("已验证:IGXE/17173","3154 / 盾没了才上高",
    "3154源自震中杯EG带盾上高盾消失被团灭"))

# ══════════════ CARRY 核心 ══════════════
add("carry_no_conversion","carry",3,["roast","meme"],
  {"all":[C("gold_share",">=",0.25),C("tower_damage","<=",1000),C("damage_share","<=",0.16)]},
  ["gold_share","tower_damage","damage_share"],
  {"tag":"大哥失格","fact":"经济占比 {gold_share}，伤害 {damage_share}，塔伤 {tower_damage}。",
   "quip":"老大老大，抬我们一手——抬不动。",
   "verdict":"本庭认定：核心位未能承担 carry 职责。",
   "share":"吃 {gold_share} 经济，打 {damage_share} 输出",
   "safe":"核心位资源转化效率偏低，建议复盘参战与推进时机。"},
  [{"quip":"大哥抬一手。手抬不起来。"},
   {"quip":"我一个人打五个——打不过五个。"}],
  ["data_incomplete","victory"],
  S("已验证:《电子竞技》OB专题","老大老大，抬我们一手 / 我一个人打五个"))

add("carry_solid","carry",0,["praise","fact"],
  {"all":[C("damage_share",">=",0.30),C("gold_share",">=",0.24)]},["damage_share","gold_share"],
  {"tag":"大哥到位","fact":"伤害占比 {damage_share}，经济 {gold_share}，资源转化到位。",
   "quip":"这才叫大哥，抬得动。","safe":"核心位表现良好。"},
  None,["defeat"],S("产品自拟",""))

# ══════════════ NEUTRAL 缓冲 ══════════════
add("neutral_team_collapse","neutral",1,["comfort","meme"],
  {"all":[C("duration","<=",1500)]},["duration"],
  {"tag":"全队连坐","fact":"比赛 {duration} 结束，全队均未建立优势。",
   "quip":"短痛。这局没有单独的被告。",
   "verdict":"本庭认定：全队共同责任，不单独定罪。",
   "safe":"本局崩盘较早，建议整体复盘 BP 与开局思路。"},
  [{"quip":"病友局，ICU 大乱斗。"},{"quip":"讲道理这把的阵容就是雪崩啊。"}],
  None,
  S("已验证:dota2.com.cn/YYF","短痛 / 病友局 / 讲道理这把的阵容就是雪崩啊",
    "短痛=按输的时长划分痛苦等级"))

add("neutral_data_incomplete","neutral",0,["court","fact"],None,[],
  {"tag":"证据不足","fact":"本局未完成解析，部分证据不可用。",
   "quip":"卷宗残缺，本庭暂不受理。",
   "verdict":"本庭认定：证据不足，不予定罪。",
   "safe":"比赛数据未完整解析，无法进行详细归因。"},
  [{"quip":"没有证据的指控，不是审判，是私刑。"}],
  None,S("产品自拟","","数据不完整时的兜底"))

add("neutral_no_guilty","neutral",0,["court","comfort"],None,[],
  {"tag":"无罪释放","fact":"本局各项数据均在合理区间。",
   "quip":"今日休庭，各回各家。",
   "verdict":"本庭认定：无人显著失职，全体无罪释放。",
   "safe":"本局团队表现均衡。"},
  [{"quip":"没找到锅。散了吧。"}],None,S("产品自拟",""))

add("neutral_close_game","neutral",1,["comfort"],
  {"all":[C("duration",">=",2700)]},["duration"],
  {"tag":"究极长痛","fact":"鏖战 {duration}，双方胶着。",
   "quip":"究极长痛。输成这样也算尽力了。",
   "safe":"本局时长较久，双方实力接近。"},
  None,None,S("dota2.com.cn术语文","短痛/中痛/长痛/究极长痛"))

# ══════════════ VICTORY 胜利（最小集，暂不扩展）══════════════
add("victory_mvp","victory",0,["praise"],
  {"all":[C("damage_share",">=",0.32)]},["damage_share"],
  {"tag":"本场 MVP","fact":"伤害占比 {damage_share}，全队第一。",
   "quip":"这波是真的强，不是「这波我很强」的那种强。",
   "share":"MVP · {damage_share} 输出","safe":"本局表现突出。"},
  None,["defeat"],S("YYF语录反用","这波我很强！","原句是自信过头下一秒被反杀"))

# ══════════════ COURT 法庭世界观 ══════════════
for i,(t,q) in enumerate([
    ("传唤","传被告到庭。"),("开庭","卷宗已备妥，五人到齐即可开庭。"),
    ("举证","呈上第一项罪证。"),("提名","本庭已列出嫌疑人名单。"),
    ("投票","实名投票，六十秒内落槌。"),("宣判","法槌落下，判决生效。"),
    ("判后","判后教育：以下建议基于本局真实数据。"),
    ("休庭","今日休庭，暂无新案卷。"),("结案","本案已归档，可随时调阅。"),
    ("上诉","被告有权作最后陈述。"),
]):
    add(f"court_{i:02d}","court",0,["court"],None,[],
        {"tag":t,"quip":q,"safe":q},None,None,S("产品自拟",""))

# ══════════════ LOADING 加载 ══════════════
for i,(t,q) in enumerate([
    ("阅卷","法官正在阅卷……"),("调取","正在调取比赛录像……"),
    ("核验","正在核验数据字段……"),("整理","正在整理证据链……"),
    ("拟判","正在草拟判词……"),
    ("解析中","OpenDota 正在解析，可能需要一分钟。"),
    ("解析失败","解析失败。本庭仅能依据速报数据。"),
    ("超时","等待超时。部分证据不可用，判决将下调严重度。"),
]):
    add(f"loading_{i:02d}","loading",0,["court","fact"],None,[],
        {"tag":t,"quip":q,"safe":q},None,None,S("产品自拟",""))

# ══════════════ SHARE 分享 ══════════════
for i,(t,q) in enumerate([
    ("判决书","{player} 被判本局大÷"),("案卷","第 {n} 号案卷已结"),
    ("战报","五黑今日战报"),("传票","一件惨案等待审理"),
    ("无罪","本庭今日无人定罪"),("翻旧账","翻出了 {player} 的旧案底"),
]):
    add(f"share_{i:02d}","share",0,["court"],None,[],
        {"tag":t,"share":q,"quip":q,"safe":q},None,None,S("产品自拟",""))

# ══════════════ 写出 ══════════════
db = {
  "version":"1.0.0","updated":"2026-07-30",
  "meta":{
    "total":len(E),
    "sources":["贴吧","NGA","B站","知乎","17173","BUFF163","IGXE","dota2.com.cn","游戏内语音","主播口头禅"],
    "corpus_size":387,
    "note":"词库是表达层，不是归因引擎。所有 trigger 只能引用 metrics 白名单。详见 SCHEMA.md"
  },
  "metrics":METRICS,"entries":E
}
out = os.path.join(HERE,"memes.json")
json.dump(db, open(out,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"写出 {len(E)} 条词条 → memes.json")
