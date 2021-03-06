import json
import nltk
import regex
import re
import datetime
import argparse
import goal_filling
import sacrebleu

parser = argparse.ArgumentParser(description='input delimiter. [ks]xxx[ks]xxx[ke]xxx[gs]xxx[gs]')
parser.add_argument('--knowledge_sep', type=str, default=' ')
parser.add_argument('--knowledge_end', type=str, default='φ')
parser.add_argument('--conversation_sep', type=str, default='φ')
parser.add_argument('--goal_stage_sep', type=str, default=' ')
parser.add_argument('--bot_in_history', type=bool, default=True, help='whether bot response appear in history')
parser.add_argument('--force_history', type=bool, default=False, help='normally if knowledges were found, no history \
would needed')
parser.add_argument('--max_history_length', type=int, default=128)
parser.add_argument('--max_goal_stage_in_history', type=int, default=2)
parser.add_argument('--train_json', type=str, default='dev.json')
parser.add_argument('--train_source_file', type=str, default='valid_with_knowledge.src')
parser.add_argument('--train_target_file', type=str, default='valid_with_knowledge.zh_CN')

args = parser.parse_args()


eq_relations = {
    '星座': [],
    '血型': [],
    '属相': ['属 啥', '属 什么', ],
    '成就': [],
    '主演': ['谁 演 的'],
    '类型': ['什么 歌'],
    '评论': [],  # ['唱 的 咋样', '评价', '好听 吗'],
    '导演': ['谁 导 的'],
    '简介': ['介绍', '信息'],
    '演唱': ['谁 唱 的', '主唱', '谁 的 歌'],
    '身高': ['多高', '多 高'],
    '体重': ['多重'],
    '获奖': ['哪些 奖', '什么 成就'],
    '口碑': [],
    '生日': ['什么 时候 出生', '哪 年 出生', '哪年 出生', '哪一年 出生', '的 出生 ', '出生日期', '多会 出生'],
    '出生地': ['哪里 的 人', '哪儿 的 人', '哪里 出生', '在 哪 出生', '哪儿 出生', '哪 的', '哪里 的 籍贯', '哪里 人', '出生 地区', '出生 在 哪里'],
    '国家地区': ['国家 地区', '哪个 国家'],
    '人均价格': ['人均 价格', '消费 怎么样', '人均 消费', '平均价格'],
    '地址': ['在 哪', '具体位置'],
    '评分': ['多少 分'],
    '特色菜': [],
    '日期': [],  # '问 日期' in goal[0]
    '时间': [],  # '问 时间' in goal[0]
    '新闻': ['故事'],
    '天气': []
}


def validate(date_text):
    try:
        datetime.datetime.strptime(date_text, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def check_relation(rel, qa):
    possible_rel = (eq_relations[rel] if rel in eq_relations.keys() else []) + [rel]
    for r in possible_rel:
        if r in qa:
            return True
    return False


def cal_score(triple, q, a):
    """
    calculate if the triple(entity, relation, something) appears in the questions and answer
    """

    # song or movie comments for recommendation
    if ('movie' in triple[0] or 'song' in triple[0]) and '评论' in triple[1]:
        if triple[0] in q or triple[0] not in a:
            return -2
        # if any part of sentences likely to appear in answer, use it
        # for sp in triple[3]:
            # sp = sp.split()
            # if len(sp) < 3:
                # continue
            # common_words = [word for word in sp if (word in a and word not in ['', ' '])]  # match by word
            # if len(common_words) / len(sp) > 0.8:
                # return 4
        return 4

    if len(triple[2]) == 0 or len(triple[3]) == 0:
        return 0  # something empty like ["异灵灵异-2002", "评论", ""]
    qa = q + ' ' + a
    score = 1 if (triple[0].replace(' ', '') in qa.replace(' ', '') or ('天气' == triple[1] and '天气' in q)) else -1  # left entity appears
    score += check_relation(triple[1], a)  # relation appears
    if triple[1] == '出生地' and score < 2:
        score -= 4  # probably not use birthplace knowledege
    if triple[2] in a:  # something directly appears
        score += 2
    else:
        common_words = [word for word in triple[3] if (word in (a if triple[1] == '新闻' else qa) and word not in ['', ' '])]  # match by word
        if (triple[1] not in ['成就', '获奖'] and len(common_words) / len(triple[3]) > 0.5) or (len(common_words) > 0 and triple[1] in ['评论', '出生地']):  # comments and birth places are more loose
            score += 2
        else:
            score -= 2

    return score


with open(args.train_json, 'r') as f:
    x = f.readlines()

with open('valid_hypo (1).txt', 'r') as f:
    valid = f.readlines()
valid_entity = open('valid_hypo_entity.txt', 'w')

src = open(args.train_source_file, 'w')
tgt = open(args.train_target_file, 'w')
len_src = 0
len_tgt = 0

difficult_info_mask = {'身高': 'height', '体重': 'weight', '评分': 'rating', '地址': 'address',
                       '人均价格': 'expense', '导演': 'director', '姓名': 'name'}
news_response = dict()
news_chat = dict()
select_comments_dict = dict()
comments_score = dict()
problembatic_entity = dict()
line_no = 0
comment_recommends = dict()
celebrity_chat = dict()
for i in x:
    line_no += 1
    i = json.loads(i)
    conversation = i['conversation']
    # conversation = [c + ' 。' if c[-1] not in ['！', '？', '。'] else c for c in conversation]
    conversation = [c.replace(i['user_profile']['姓名'], 'name') for c in conversation]  # replace user's name

    goal = i['goal'].split(' --> ')
    kg = i['knowledge']

    # fix bug when song contain "   "
    for j in range(len(kg)):
        if kg[j][1] != '评论' and '评论' in kg[j][1]:
            kg[j][0] += ' ' + kg[j][1].replace(' 评论', '')
            kg[j][1] = '评论'
        if kg[j][1] == '新闻':  # not use news in knowledge to avoid multiple identical news
            kg[j][2] == 'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz'

    # entity replacement

    entity_cnt = 0
    entity_dict = dict()
    goal_info = [goal_filling.extract_info_from_goal(g) for g in goal] if '--> ...... -->' not in goal else goal_filling.fill_test(i)
    # print(goal_info)
    # input()
    for gi in goal_info:
        if len(gi) == 4:  # news
            kg.append([gi[2], '新闻', gi[3]])
            continue
        if len(gi) != 3 or gi[1] not in ['兴趣点 推荐', '电影 推荐', '播放 音乐', '音乐 推荐', '音乐 点播']:
            continue
        entities = gi[2] if type(gi[2]) is type(list()) else [gi[2]]
        entity_cnt += len(entities)
        for entity in entities[::-1]:
            # no redundant entity
            if entity in entity_dict.keys():
                continue
            entity_no = ''
            if '兴趣点 推荐' == gi[1]:
                entity_cnt -= 1
                entity_no = 'restaurant_' + str(entity_cnt)
            if '电影 推荐' == gi[1]:
                entity_cnt -= 1
                entity_no = 'movie_' + str(entity_cnt)
            if '播放 音乐' == gi[1] or '音乐 推荐' == gi[1] or '音乐 点播' == gi[1]:
                entity_cnt -= 1
                entity_no = 'song_' + str(entity_cnt)
            if entity_no == '':
                continue
            entity_dict[entity] = entity_no

            # solve recuresive　name like 欺诈 的 碎片 与 惊心 的 情感 ： 《 色 · 戒 》 纪实 and 色 · 戒
            entity_appear = False
            for j in range(len(conversation)):
                if entity in conversation[j]:
                    entity_appear = True
                    conversation[j] = conversation[j].replace(entity, entity_no)
            if not entity_appear:
                if entity not in problembatic_entity.keys():
                    problembatic_entity[entity] = [line_no]
                else:
                    problembatic_entity[entity].append(line_no)
            for j in range(len(kg)):
                if kg[j][0].replace(' ', '') == entity.replace(' ', ''):
                    kg[j][0] = entity_no
                kg[j][2] = kg[j][2].replace(entity, entity_no)
        entity_cnt += len(entity_dict)







    user_first = True if 'User 主动' in goal[0] else False
    bot_hello = False
    if '寒暄' in goal[0]:
        bot_hello = True
        hello_info = ['寒暄', i['situation']]
        if '带 User 名字' in goal[0]:
            hello_info.append('name')
            hello_info.append(i['user_profile']['性别'])
            if '年龄区间' in str(i['user_profile']):
                hello_info.append(i['user_profile']['年龄区间'])
        print(*hello_info, file=src, sep=args.knowledge_sep, end='')
        print(args.knowledge_end, file=src)
        len_src += 1

    # add reverse knowledge
    # for j in range(len(kg)):
        # if kg[j][1] in ['生日', '演唱', '导演', '主演', '星座']:
            # kg.append([kg[j][2], kg[j][1], kg[j][0]])

    for j in range(len(kg)):

        if validate(kg[j][1]):
            kg[j][1] = '天气'  # weather knowledge relation always in date form
        if kg[j][1] in ['评论']:
            kg[j].append(re.split('[！？，。]', kg[j][2]))  # result example ["高中 时候 很 喜欢 他 的 歌 ", " 后面 就 只是 关注 他 的 生活 了 。"]
        else:
            if kg[j][1] == '出生地':
                kg[j].append(kg[j][2].split('   '))  # input example ["谢娜", "出生地", "中国   四川   德阳   中 江"]
            else:
                kg[j].append(kg[j][2].split(' '))  # match by words

        for k in kg[j][3]:
            if k in ['', ' ', '，', '。', '！', '。', '~', '-'] or\
            (len(kg[j][2]) > 4 and len(k) < 4 and kg[j][1] in ['评论']):  # unhelpful in matching
                kg[j][3].remove(k)



    if len(goal) == 0:
        continue

    used_k = set()  # used string-format knowledge tuple in previous conversation

    goal_stage_milestone = set()
    for j in range(len(conversation)-1):
        if conversation[j][0] == '[':
            goal_stage_milestone.add(j)
            current_goal_stage = len(goal_stage_milestone)
            # conversation[j] = conversation[j][4:]
        using_k = set()  # bot need these knowledge tuples to answer
        user_round = user_first ^ (j & 1)  # True if it were user speaking

        # if not user_round:
            # for k, v in zip(entity_dict.keys(), entity_dict.values()):
                # valid[0] = valid[0].replace(v, k)
            # print(valid[0].strip(), file=valid_entity)
            # valid = valid[1:]

        qa = conversation[j].strip() + ' ' + conversation[j + 1].strip()
        history = ' '.join(conversation[:j])

        # find the recommend song or movie
        #print(goal_info[current_goal_stage-1:], conversation[j+1])
        recommend_stage = '推荐' in goal_info[current_goal_stage - 1][1] or \
                          (current_goal_stage != len(goal_info) and conversation[j + 1][0] == '[' and '推荐' in goal_info[current_goal_stage][1])
        recommend_item = ''
        for gi in goal_info[current_goal_stage-1:current_goal_stage+1]:
            if len(gi) == 3 and type(gi[2]) is type(list()):
                for r in gi[2]:
                    r = entity_dict[r]
                    if r in conversation[j+1] and r not in history:
                        recommend_item = r
                        break

        # print(recommend_item, goal_info[current_goal_stage-1:current_goal_stage+1], entity_dict, qa)
        for k in kg:
            if str(k[:3]) in used_k:  # assume that no knowledge will be used twice
                continue

            if k[1] == '导演' and '   ' in k[2]:  # ["喜剧之王", "导演", "周星驰   李 力持"]
                k[2] = k[2].replace('   ', ' ， ')

            if (k[1] == '天气' and ((goal_info[1][1] == '天气 信息 推送' and j == 2) or  # from these goals we can know what knowledge to use
                                        (goal_info[0][1] == '问 天气' and j == 0))) or \
                    ('适合' in k[1] and j == 2) or \
                    ('生日' == k[1] and goal_info[0][1] == '问 日期' and j == 2):
                #print(k[:3])
                using_k.add(str(k[:3]))
                continue
            score = cal_score(k, history + ' ' + conversation[j].strip(), conversation[j+1].strip())


            if score > max(2, cal_score(k, history, conversation[j].strip())):  # this knowledge might appear and not appear before
                
                if k[1] == '新闻':  # collect how bot broadcast news
                    news_response[k[2]] = conversation[j+1]
                    # print(k[2], conversation[j+1])

                if k[1] in difficult_info_mask.keys() and k[2] in conversation[j+1]:

                    conversation[j+1] = re.sub('(^'+k[2]+')|( '+k[2] + ' )|( ' + k[2] + '$)', ' ' + difficult_info_mask[k[1]] + ' ', conversation[j+1])
                    # print(conversation[j+1])
                    # conversation[j+1].replace(' ' + k[2] + ' ', ' ' + difficult_info_mask[k[1]] + ' ')
                    # print(conversation[j+1])
                    k[2] = difficult_info_mask[k[1]]
                
                if k[1] == '评论':  # comment only used in recommendation
                    k[2] = k[2][:88]
                    if '推荐' not in goal_info[current_goal_stage-1][1] and (len(goal_info) == current_goal_stage or '推荐' not in goal_info[current_goal_stage][1]):
                        continue

                used_k.add(str(k[:3]))
                if user_round:  # we only need simulate bot
                    using_k.add(str(k[:3]))  # using sest to remove redundant tuple such as multiple celebrity birthday
                if '新闻' in k[1]:  # avoid multiple news knowledge
                    break

        if j == 0 and ('问 日期' in goal[0] or '问 时间' in goal[0]):  # situation not in knowledge
            using_k = {i['situation']}

        if user_round:
            # add goal transition
            goal_transition = goal_info[current_goal_stage - 1:current_goal_stage + 1]
            if '新闻' in goal_transition[0][1]:
                goal_transition[0][3] = ''
            if len(goal_transition[0]) > 2 and type(goal_transition[0][2]) == type(list):
                goal_transition[0][2] = [entity_dict[e] for e in goal_transition[0][2]]
            if len(goal_transition) > 1 and '新闻' in goal_transition[1][1]:
                goal_transition[1][3] = ''
            if len(goal_transition) > 1 and len(goal_transition[1]) > 2 and type(goal_transition[1][2]) == type(list):
                goal_transition[1][2] = [entity_dict[e] for e in goal_transition[1][2]]
            goal_transition = str(goal_transition)
            for k, v in zip(entity_dict.keys(), entity_dict.values()):
                goal_transition = goal_transition.replace(k, v)
            print(goal_transition, end=args.knowledge_end, file=src)
            if len(using_k) != 0:

                if goal_info[current_goal_stage-1][1] == '关于 明星 的 聊天' and conversation[j+1][0] != '[':  # collect knowledge and corresponding response when chatting about celebrity
                    celebrity_chat[str(using_k)] = conversation[j+1]
                
                # use only comment knowledge when recommending
                if recommend_item != '':
                    best_comment = ''
                    max_bleu = -1.0
                    for uk in using_k:
                        if recommend_item in uk and '评论' in uk:
                            uk = eval(uk)
                            comment = uk[2]
                            bleu = sacrebleu.corpus_bleu([conversation[j+1]], [[comment]]).score
                            # print(bleu, comment)
                            if bleu > max_bleu:
                                song = ''
                                for entity in entity_dict.keys():
                                    if uk[0] == entity_dict[entity]:
                                        song = entity
                                max_bleu = bleu
                                if song != '':
                                    if song not in select_comments_dict:
                                        select_comments_dict[song] = {str(uk).replace(entity_dict[song], song)}
                                    else:
                                        select_comments_dict[song].add(str(uk).replace(entity_dict[song], song))
                                best_comment = str(uk)
                                
                                if song != '':
                                    uk = str(uk).replace(entity_dict[song], song)
                                    comment_recommend = conversation[j+1].replace(entity_dict[song], song)
                                    if uk not in comment_recommends.keys():
                                        comment_recommends[uk] = {comment_recommend}
                                    else:
                                        comment_recommends[uk].add(comment_recommend)
                    # if best_comment == '':
                    # print(max_bleu, best_comment, qa)
                    using_k = {best_comment}
                print(*using_k, sep=args.knowledge_sep, end='', file=src)  # knowledge at the beginning of user question
            print(args.knowledge_end, end='', file=src)
            if 0 < j < len(conversation) - 2 and (len(using_k) == 0 or args.force_history):  # say good bye need not history information

                # add history
                add_history = []
                pos_h = j - 1
                delta_goal_stage = 0
                while len(' '.join(add_history) + conversation[pos_h]) < args.max_history_length and pos_h >= 0:
                    add_history.append(conversation[pos_h].strip())
                    if pos_h in goal_stage_milestone:
                        add_history[-1] += args.goal_stage_sep
                        delta_goal_stage += 1
                        if delta_goal_stage >= args.max_goal_stage_in_history:
                            break
                    pos_h -= 1
                # add_history = [c + ' 。' if c[-1] not in ['！', '？', '。'] else c for c in add_history]
                print(*add_history[::-1], sep=args.conversation_sep, end=args.conversation_sep, file=src)
        print(conversation[j] + args.goal_stage_sep, file=src if user_round else tgt)
        if user_round:
            len_src += 1
        else:
            len_tgt += 1
    if len_tgt == len_src - 1:
        print(conversation[-1], file=tgt)  # when bot say goodbye first, user's goodbye would be ignored.
        len_tgt += 1

        for k, v in zip(entity_dict.keys(), entity_dict.values()):
            valid[0] = valid[0].replace(v, k)
        print(valid[0], file=valid_entity)
        valid = valid[1:]

    assert len_src == len_tgt

print(problembatic_entity)

with open('dialog_celebrity_chat_train.txt', 'w') as f:
    print(celebrity_chat, file=f)
with open('dialog_comment_recommends_train.txt', 'w') as f:
    print(comment_recommends, file=f)

'''
with open('dialog_comment_recommends_merge.txt', 'r') as f:
    comment_recommends_train = f.readline()
    comment_recommends_train = eval(comment_recommends_train)

for k, v in zip(comment_recommends_train.keys(), comment_recommends_train.values()):
    if k not in comment_recommends.keys():
        comment_recommends[k] = v
    else:
        for vi in v:
            comment_recommends[k].add(vi)
with open('dialog_comment_recommends_merge.txt', 'w') as f:
    print(comment_recommends, file=f)


with open('dialog_celebrity_chat_merge.txt', 'r') as f:
    celebrity_chat_train = f.readline()
    celebrity_chat_train = eval(celebrity_chat_train)

for k, v in zip(celebrity_chat_train.keys(), celebrity_chat_train.values()):
    if k not in celebrity_chat.keys():
        celebrity_chat[k] = v
with open('dialog_celebrity_chat_merge.txt', 'w') as f:
    print(celebrity_chat, file=f)



with open('dialog_news_response.txt', 'w') as f:
    print(news_response, file=f)

with open('dialog_select_comment_dict.txt', 'w') as f:
    print(select_comments_dict, file=f)
'''
