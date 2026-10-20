package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.*;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import java.io.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.zip.CRC32;

/** PNG tEXt/JSON transport only: no scripts, URLs, regexes or model prompt overrides execute. */
@Component
public class CharacterCardCodec {
    public static final int MAX_FILE=3*1024*1024,MAX_JSON=512*1024;
    private static final byte[] SIGNATURE={(byte)137,80,78,71,13,10,26,10};
    private final ObjectMapper json;
    public CharacterCardCodec(ObjectMapper json){this.json=json;}
    public record Parsed(ObjectNode source, ObjectNode config, byte[] avatar,List<String> warnings){}
    public Parsed parse(String format,String encoded) {
        try {
            if(encoded==null||encoded.length()>MAX_FILE*4/3+8)throw invalid("文件过大，最多3MB");
            byte[] bytes=Base64.getDecoder().decode(encoded),avatar=null;String text;
            if("png".equals(format)) {
                var chunks=chunks(bytes);String v2=null,v3=null;var out=new ByteArrayOutputStream();out.write(SIGNATURE);
                for(var c:chunks) {
                    if(c.type.equals("tEXt")) {
                        int zero=0;while(zero<c.data.length&&c.data[zero]!=0)zero++;
                        if(zero<c.data.length) {
                            String key=new String(c.data,0,zero,StandardCharsets.ISO_8859_1).toLowerCase(Locale.ROOT);
                            if(Set.of("chara","ccv3").contains(key)) {
                                byte[] value=Base64.getDecoder().decode(Arrays.copyOfRange(c.data,zero+1,c.data.length));
                                if(value.length>MAX_JSON)throw invalid("角色定义过大");
                                String decoded=new String(value,StandardCharsets.UTF_8);
                                if(key.equals("ccv3")){if(v3!=null)throw invalid("重复的角色定义");v3=decoded;}
                                else {if(v2!=null)throw invalid("重复的角色定义");v2=decoded;}
                            }
                        }
                    }
                    if(Set.of("IHDR","PLTE","IDAT","tRNS","IEND","sRGB","gAMA").contains(c.type))out.write(chunk(c.type,c.data));
                }
                text=v3!=null?v3:v2;if(text==null)throw invalid("这张PNG没有嵌入角色定义，请下载角色卡原文件");
                avatar=out.toByteArray();
            } else if("json".equals(format)){if(bytes.length>MAX_JSON)throw invalid("角色定义过大");text=new String(bytes,StandardCharsets.UTF_8);}
            else throw invalid("仅支持PNG和JSON角色卡");
            var root=json.readTree(text);if(!root.isObject())throw invalid("角色定义必须是JSON对象");
            String spec=root.path("spec").asText("");
            if(!spec.isBlank()&&!Set.of("chara_card_v2","chara_card_v3").contains(spec))throw invalid("不支持这个角色卡版本");
            JsonNode data=spec.isBlank()?root:root.path("data");if(!data.isObject())throw invalid("角色卡缺少data");
            ObjectNode config=json.createObjectNode();config.put("name",field(data,"name",80));config.put("description",field(data,"description",8000));
            config.put("personality",field(data,"personality",4000));config.put("scenario",field(data,"scenario",4000));
            config.put("firstMessage",field(data,"first_mes",4000));config.put("examples",field(data,"mes_example",12000));
            config.put("tone","自然");config.put("responseStyle","遵循角色的交流习惯");config.put("customInstructions","");config.put("forbiddenExpressions","");
            var own=data.path("extensions").path("psych_agent_voice");
            if(own.isObject())for(String key:List.of("tone","responseStyle","customInstructions","forbiddenExpressions"))if(own.path(key).isTextual())config.set(key,own.get(key));
            List<String> warnings=new ArrayList<>();
            if(!data.path("system_prompt").asText("").isBlank()||!data.path("post_history_instructions").asText("").isBlank())warnings.add("原卡的系统提示覆盖会保留在导出文件中，但不会执行。");
            if(data.has("assets")||!data.path("extensions").isEmpty())warnings.add("远程素材和扩展仅保留，不加载或执行。");
            var book=json.createObjectNode();book.put("name",data.path("character_book").path("name").asText("背景设定"));book.put("scanDepth",own.path("worldBook").path("scanDepth").asInt(0));
            var entries=book.putArray("entries");var original=data.path("character_book").path("entries");
            if(original.isArray()) {
                if(original.size()>64)throw invalid("世界书最多64条，请精简后导入");
                int sourceIndex=0;
                for(var e:original) {
                    int originalIndex=sourceIndex++;
                    if(e.path("use_regex").asBoolean(false)||e.path("extensions").path("use_regex").asBoolean(false)) {warnings.add("已跳过使用正则表达式的世界书条目。");continue;}
                    var row=entries.addObject();row.put("id",UUID.randomUUID().toString());row.put("sourceIndex",originalIndex);
                    String name=e.path("name").asText(e.path("comment").asText("背景条目"));row.put("name",name.substring(0,Math.min(80,name.length())));
                    row.put("content",field(e,"content",3000));row.set("keys",e.has("keys")?e.get("keys"):json.createArrayNode());
                    row.set("secondaryKeys",e.has("secondary_keys")?e.get("secondary_keys"):json.createArrayNode());
                    row.put("enabled",e.path("enabled").asBoolean(true));row.put("constant",e.path("constant").asBoolean(false));
                    row.put("selective",e.path("selective").asBoolean(false));row.put("caseSensitive",e.path("case_sensitive").asBoolean(false));
                    row.put("priority",Math.max(0,Math.min(100,e.path("priority").asInt(50))));
                }
                if(!entries.isEmpty())warnings.add("世界书按简化规则使用：常驻或关键词触发，不执行递归、脚本和插入位置覆盖。");
            }
            config.set("worldBook",book);
            return new Parsed((ObjectNode)root,normalize(config),avatar,warnings.stream().distinct().toList());
        }catch(ResponseStatusException e){throw e;}catch(Exception e){throw invalid("无法读取角色卡，文件可能损坏或格式不受支持");}
    }
    public ObjectNode normalize(JsonNode input) {
        if(input==null||!input.isObject())throw invalid("角色配置不能为空");
        var out=json.createObjectNode();
        for(var pair:Map.of("name",80,"description",8000,"personality",4000,"scenario",4000,"firstMessage",4000,"examples",12000,"tone",80,"responseStyle",200,"customInstructions",3000,"forbiddenExpressions",1000).entrySet())out.put(pair.getKey(),field(input,pair.getKey(),pair.getValue()));
        if(out.path("name").asText().isBlank())throw invalid("请填写角色名称");
        var book=out.putObject("worldBook");var source=input.path("worldBook");book.put("name",field(source,"name",80));
        int depth=source.path("scanDepth").asInt(0);if(depth!=0&&depth!=2)throw invalid("背景扫描范围只能是当前消息或最近两条用户消息");book.put("scanDepth",depth);
        var entries=book.putArray("entries");var rows=source.path("entries");if(!rows.isMissingNode()&&(!rows.isArray()||rows.size()>64))throw invalid("世界书最多64条");
        Set<String> ids=new HashSet<>();
        if(rows.isArray())for(var row:rows) {
            var entry=entries.addObject();String id=field(row,"id",80);if(id.isBlank())id=UUID.randomUUID().toString();if(!ids.add(id))throw invalid("背景条目编号重复");entry.put("id",id);
            if(row.path("sourceIndex").isInt()&&row.path("sourceIndex").asInt()>=0&&row.path("sourceIndex").asInt()<64)entry.put("sourceIndex",row.path("sourceIndex").asInt());
            entry.put("name",field(row,"name",80));entry.put("content",field(row,"content",3000));
            if(entry.path("content").asText().isBlank())throw invalid("背景内容不能为空");
            for(String key:List.of("keys","secondaryKeys")) {
                var list=entry.putArray(key);var values=row.path(key);if(!values.isMissingNode()&&(!values.isArray()||values.size()>12))throw invalid("每条背景最多12个关键词");
                if(values.isArray())for(var value:values){if(!value.isTextual()||value.asText().isBlank()||value.asText().length()>60)throw invalid("关键词需要是1至60字的文本");list.add(value.asText().strip());}
            }
            for(String key:List.of("enabled","constant","selective","caseSensitive")){if(row.has(key)&&!row.get(key).isBoolean())throw invalid("背景开关格式不正确");entry.put(key,row.path(key).asBoolean(key.equals("enabled")));}
            int priority=row.path("priority").asInt(50);if(priority<0||priority>100)throw invalid("背景优先级需为0至100");entry.put("priority",priority);
            if(!entry.path("constant").asBoolean()&&entry.path("keys").isEmpty())throw invalid("非固定背景需要至少一个关键词");
            if(entry.path("selective").asBoolean()&&entry.path("secondaryKeys").isEmpty())throw invalid("组合触发需要辅助关键词");
        }
        return out;
    }
    public byte[] embed(byte[] image,String document) {
        try {var out=new ByteArrayOutputStream();out.write(SIGNATURE);
            for(var c:chunks(image)){if(c.type.equals("IEND")){out.write(chunk("tEXt",((json.readTree(document).path("spec").asText().equals("chara_card_v3")?"ccv3\0":"chara\0")+Base64.getEncoder().encodeToString(document.getBytes(StandardCharsets.UTF_8))).getBytes(StandardCharsets.ISO_8859_1)));}out.write(chunk(c.type,c.data));}
            return out.toByteArray();
        }catch(IOException e){throw new IllegalStateException(e);}
    }
    private record Chunk(String type,byte[] data){}
    private List<Chunk> chunks(byte[] bytes) {
        if(bytes.length<45||bytes.length>MAX_FILE||!Arrays.equals(Arrays.copyOf(bytes,8),SIGNATURE))throw invalid("不是有效PNG文件");
        ByteBuffer b=ByteBuffer.wrap(bytes).order(ByteOrder.BIG_ENDIAN);b.position(8);var rows=new ArrayList<Chunk>();boolean end=false,idat=false;
        while(b.remaining()>=12) {
            int length=b.getInt();if(length<0||length>b.remaining()-8)throw invalid("PNG数据块不完整");
            byte[] type=new byte[4],data=new byte[length];b.get(type);b.get(data);int expected=b.getInt();var crc=new CRC32();crc.update(type);crc.update(data);if((int)crc.getValue()!=expected)throw invalid("PNG校验失败");
            String t=new String(type,StandardCharsets.US_ASCII);
            if(rows.isEmpty()){if(!t.equals("IHDR")||length!=13)throw invalid("PNG缺少图像头");var size=ByteBuffer.wrap(data);int w=size.getInt(),h=size.getInt();if(w<1||h<1||w>4096||h>4096)throw invalid("头像尺寸需在4096像素以内");}
            if(t.equals("IDAT"))idat=true;rows.add(new Chunk(t,data));
            if(t.equals("IEND")){if(length!=0||b.hasRemaining())throw invalid("PNG尾部异常");end=true;break;}
            if(rows.size()>4096)throw invalid("PNG数据块过多");
        }
        if(!end||!idat)throw invalid("PNG缺少图像或结束标记");return rows;
    }
    private byte[] chunk(String type,byte[] data) {
        byte[] key=type.getBytes(StandardCharsets.US_ASCII);var crc=new CRC32();crc.update(key);crc.update(data);
        return ByteBuffer.allocate(data.length+12).putInt(data.length).put(key).put(data).putInt((int)crc.getValue()).array();
    }
    private String field(JsonNode n,String key,int max){var value=n.path(key);if(value.isMissingNode()||value.isNull())return "";if(!value.isTextual()||value.asText().length()>max)throw invalid(key+"超出长度或格式不正确");return value.asText().strip();}
    private ResponseStatusException invalid(String message){return new ResponseStatusException(HttpStatus.BAD_REQUEST,message);}
}
