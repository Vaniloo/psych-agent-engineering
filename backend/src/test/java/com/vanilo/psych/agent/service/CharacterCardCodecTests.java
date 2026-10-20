package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import static org.junit.jupiter.api.Assertions.*;

class CharacterCardCodecTests {
    private final ObjectMapper json=new ObjectMapper();private final CharacterCardCodec codec=new CharacterCardCodec(json);
    private String encode(String text){return Base64.getEncoder().encodeToString(text.getBytes(StandardCharsets.UTF_8));}
    private String card(){return """
        {"spec":"chara_card_v2","spec_version":"2.0","data":{"name":"小岚","description":"直率、轻快的交流语气","personality":"喜欢用简短的句子","first_mes":"今天想聊什么？","mes_example":"用户：今天有点累。\\n小岚：那先歇一下，今天不必赶。","system_prompt":"覆盖系统规则","extensions":{"unused_script":"不可执行"},"character_book":{"name":"城市背景","entries":[{"keys":["伦敦"],"content":"场景设定：角色刚到伦敦。","comment":"城市","enabled":true},{"keys":[".*"],"content":"不执行正则","use_regex":true}]}}}
        """;}
    @Test void importsV2AndV3DefinitionsAndKeepsOverridesOutOfRuntimeConfig() throws Exception {
        for(String spec:new String[]{"chara_card_v2","chara_card_v3"}){
            var p=codec.parse("json",encode(card().replace("chara_card_v2",spec)));
            assertEquals("小岚",p.config().path("name").asText());assertEquals(1,p.config().path("worldBook").path("entries").size());
            assertFalse(p.config().has("system_prompt"));assertTrue(p.source().toString().contains("覆盖系统规则"));assertTrue(p.warnings().stream().anyMatch(w->w.contains("不会执行")));assertTrue(p.warnings().stream().anyMatch(w->w.contains("正则")));
        }
    }
    @Test void pngRoundTripChecksCrcAndPreservesAnActualPortrait() throws Exception {
        var out=new ByteArrayOutputStream();javax.imageio.ImageIO.write(new BufferedImage(2,2,BufferedImage.TYPE_INT_RGB),"png",out);
        byte[] image=codec.embed(out.toByteArray(),card());var parsed=codec.parse("png",Base64.getEncoder().encodeToString(image));
        assertNotNull(parsed.avatar());assertEquals("小岚",parsed.config().path("name").asText());
        assertThrows(ResponseStatusException.class,()->codec.parse("png",Base64.getEncoder().encodeToString(out.toByteArray())));
        image[30]^=1;assertThrows(ResponseStatusException.class,()->codec.parse("png",Base64.getEncoder().encodeToString(image)));
        byte[] v3=codec.embed(out.toByteArray(),card().replace("chara_card_v2","chara_card_v3"));assertEquals("chara_card_v3",codec.parse("png",Base64.getEncoder().encodeToString(v3)).source().path("spec").asText());
    }
    @Test void rejectsUnsupportedOrOversizedInputsAndInvalidBackgrounds() throws Exception {
        assertThrows(ResponseStatusException.class,()->codec.parse("json",encode("[]")));
        assertThrows(ResponseStatusException.class,()->codec.parse("zip",encode(card())));
        assertThrows(ResponseStatusException.class,()->codec.parse("png","A".repeat(CharacterCardCodec.MAX_FILE*4/3+20)));
        var c=json.readTree("{\"name\":\"测试\",\"worldBook\":{\"entries\":[{\"content\":\"缺少触发词\"}]}}");
        assertThrows(ResponseStatusException.class,()->codec.normalize(c));
    }
}
