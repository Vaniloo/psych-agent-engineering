package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.*;
import com.vanilo.psych.agent.entity.*;
import com.vanilo.psych.agent.repository.*;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import java.time.LocalDateTime;
import java.util.*;

@Service
@Transactional
public class RoleStudioService {
    private final RoleStudioAccess access;private final CharacterCardCodec codec;private final ObjectMapper json;
    private final RoleCardRepository cards;private final UserRepository users;
    public RoleStudioService(RoleStudioAccess access,CharacterCardCodec codec,ObjectMapper json,RoleCardRepository cards,UserRepository users){this.access=access;this.codec=codec;this.json=json;this.cards=cards;this.users=users;}
    public Map<String,Object> features(String username){return Map.of("enabled",access.allowed(username),"version","voice-worldbook-preview-v1");}
    public Map<String,Object> preview(String username,String format,String data){access.require(username);var p=codec.parse(format,data);return preview(p);}
    private Map<String,Object> preview(CharacterCardCodec.Parsed p){var result=new LinkedHashMap<String,Object>();result.put("config",p.config());result.put("warnings",p.warnings());if(p.avatar()!=null)result.put("avatarDataUrl","data:image/png;base64,"+Base64.getEncoder().encodeToString(p.avatar()));return result;}
    public Map<String,Object> importCard(String username,String format,String data){access.require(username);var p=codec.parse(format,data);var card=new RoleCard();card.setOwner(owner(username));card.setPreset(false);card.setExperimental(true);card.setCreatedAt(LocalDateTime.now());card.setSourceCardJson(p.source().toString());card.setAvatarPng(p.avatar());apply(card,p.config());cards.saveAndFlush(card);return detail(username,card.getId());}
    public Map<String,Object> save(String username,Long id,JsonNode config){access.require(username);var normalized=codec.normalize(config);RoleCard card;
        if(id==null){card=new RoleCard();card.setOwner(owner(username));card.setPreset(false);card.setExperimental(true);card.setCreatedAt(LocalDateTime.now());}
        else{card=readable(username,id);if(card.isPreset()||!Boolean.TRUE.equals(card.getExperimental())||!Objects.equals(card.getOwner().getId(),owner(username).getId()))throw new ResponseStatusException(HttpStatus.FORBIDDEN,"请先复制为实验角色再修改");}
        apply(card,normalized);cards.saveAndFlush(card);return detail(username,card.getId());}
    public Map<String,Object> copy(String username,Long id){access.require(username);var original=readable(username,id);var config=config(original);config.put("name",(original.getName()+" · 副本").substring(0,Math.min(80,original.getName().length()+5)));var result=save(username,null,config);var created=cards.findById(((Number)result.get("id")).longValue()).orElseThrow();created.setAvatarPng(original.getAvatarPng());created.setSourceCardJson(original.getSourceCardJson());cards.saveAndFlush(created);return detail(username,created.getId());}
    public Map<String,Object> detail(String username,Long id){access.require(username);var card=readable(username,id);var result=new LinkedHashMap<String,Object>();result.put("id",card.getId());result.put("config",config(card));result.put("editable",Boolean.TRUE.equals(card.getExperimental())&&!card.isPreset());result.put("experimental",Boolean.TRUE.equals(card.getExperimental()));if(card.getAvatarPng()!=null)result.put("avatarDataUrl","data:image/png;base64,"+Base64.getEncoder().encodeToString(card.getAvatarPng()));return result;}
    public Map<String,Object> configuration(String username){if(!access.allowed(username))return Map.of();var user=owner(username);var card=user.getActiveRoleCard();if(card==null||!Boolean.TRUE.equals(card.getExperimental()))return Map.of();var result=new LinkedHashMap<String,Object>();result.put("enabled",true);result.put("cardId",card.getId());result.put("config",config(card));return result;}
    public ObjectNode exportJson(String username,Long id){access.require(username);var card=readable(username,id);var config=config(card);ObjectNode root;
        try {root=card.getSourceCardJson()==null?json.createObjectNode():(ObjectNode)json.readTree(card.getSourceCardJson()).deepCopy();}catch(Exception e){throw new IllegalStateException(e);}
        if(!root.has("spec")){root=json.createObjectNode();root.put("spec","chara_card_v2");root.put("spec_version","2.0");root.set("data",json.createObjectNode());}
        var data=(ObjectNode)root.get("data");
        for(var pair:Map.of("name","name","description","description","personality","personality","scenario","scenario","first_mes","firstMessage","mes_example","examples").entrySet())data.put(pair.getKey(),config.path(pair.getValue()).asText());
        if(!data.path("extensions").isObject())data.set("extensions",json.createObjectNode());((ObjectNode)data.get("extensions")).set("psych_agent_voice",config.deepCopy());
        if(!data.has("system_prompt"))data.put("system_prompt","");if(!data.has("post_history_instructions"))data.put("post_history_instructions","");
        if(!data.has("alternate_greetings"))data.set("alternate_greetings",json.createArrayNode());if(!data.has("tags"))data.set("tags",json.createArrayNode());if(!data.has("creator"))data.put("creator","Psych Agent experimental studio");if(!data.has("creator_notes"))data.put("creator_notes","");if(!data.has("character_version"))data.put("character_version","1.0");
        ObjectNode book=data.path("character_book").isObject()?(ObjectNode)data.get("character_book").deepCopy():json.createObjectNode();var prior=book.path("entries").deepCopy();book.put("name",config.path("worldBook").path("name").asText());var entries=book.putArray("entries");int index=0;
        for(var e:config.path("worldBook").path("entries")){ObjectNode entry=json.createObjectNode();int source=e.path("sourceIndex").asInt(-1);if(prior.isArray()&&source>=0&&source<prior.size()&&prior.get(source).isObject())entry=(ObjectNode)prior.get(source).deepCopy();entry.put("id",index++);entry.put("comment",e.path("name").asText());entry.put("content",e.path("content").asText());entry.set("keys",e.get("keys"));entry.set("secondary_keys",e.get("secondaryKeys"));for(var pair:Map.of("enabled","enabled","constant","constant","selective","selective","case_sensitive","caseSensitive","priority","priority").entrySet())entry.set(pair.getKey(),e.get(pair.getValue()));entry.put("insertion_order",index);entries.add(entry);}
        data.set("character_book",book);return root;
    }
    public byte[] exportPng(String username,Long id){access.require(username);var card=readable(username,id);byte[] image=card.getAvatarPng();if(image==null)throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"这张角色卡没有PNG头像，可以先导出JSON");return codec.embed(image,exportJson(username,id).toString());}
    private User owner(String username){return users.findByUsername(username).orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND));}
    private RoleCard readable(String username,Long id){var user=owner(username);var card=cards.findById(id).orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND));if(!card.isPreset()&&(card.getOwner()==null||!Objects.equals(card.getOwner().getId(),user.getId())))throw new ResponseStatusException(HttpStatus.NOT_FOUND);if(Boolean.TRUE.equals(card.getExperimental()))access.require(username);return card;}
    private ObjectNode config(RoleCard card){try {if(card.getStudioConfigJson()!=null)return (ObjectNode)json.readTree(card.getStudioConfigJson());}catch(Exception e){throw new IllegalStateException(e);}var c=json.createObjectNode();c.put("name",card.getName());c.put("description",card.getDescription());c.put("tone",card.getTone());c.put("responseStyle",card.getResponseStyle());c.put("customInstructions",Objects.toString(card.getCustomInstructions(),""));c.put("forbiddenExpressions",Objects.toString(card.getForbiddenExpressions(),""));return codec.normalize(c);}
    private void apply(RoleCard card,ObjectNode c){card.setName(c.path("name").asText());card.setDescription(c.path("description").asText());card.setTone(c.path("tone").asText());card.setResponseStyle(c.path("responseStyle").asText());card.setCustomInstructions(c.path("customInstructions").asText());card.setForbiddenExpressions(c.path("forbiddenExpressions").asText());card.setStudioConfigJson(c.toString());}
}
