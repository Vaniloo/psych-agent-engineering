package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.repository.UserMapper;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import static org.junit.jupiter.api.Assertions.*;

@org.springframework.boot.autoconfigure.ImportAutoConfiguration(org.mybatis.spring.boot.autoconfigure.MybatisAutoConfiguration.class)
@JdbcTest(properties={"spring.datasource.url=jdbc:h2:mem:role_studio;MODE=MySQL;DB_CLOSE_DELAY=-1","spring.datasource.driver-class-name=org.h2.Driver","ROLE_STUDIO_ENABLED=true","ROLE_STUDIO_USERS=alice"})
@AutoConfigureTestDatabase(replace=AutoConfigureTestDatabase.Replace.NONE)
@Import({RoleStudioAccess.class,CharacterCardCodec.class,RoleStudioService.class,RoleCardService.class,ObjectMapper.class})
class RoleStudioIntegrationTests {
    @Autowired RoleStudioService studio;@Autowired RoleCardService cards;@Autowired RoleStudioAccess access;@Autowired UserMapper users;@Autowired ObjectMapper json;
    @BeforeEach void setup(){for(String name:new String[]{"alice","bob"}){var u=new User();u.setUsername(name);u.setPassword("test-only");u.setRole("USER");users.insert(u);}}
    @Test void previewDoesNotPersistAndOnlyAllowedOwnerCanImportActivateAndEdit() throws Exception {
        String document="{\"spec\":\"chara_card_v2\",\"spec_version\":\"2.0\",\"data\":{\"name\":\"轻快同伴\",\"personality\":\"直率但友好\",\"mes_example\":\"用户：你好\\n角色：嗨，今天想聊什么？\"}}";
        String encoded=Base64.getEncoder().encodeToString(document.getBytes(StandardCharsets.UTF_8));
        assertNotNull(studio.preview("alice","json",encoded));assertTrue(cards.listAvailable("alice").isEmpty());
        var imported=studio.importCard("alice","json",encoded);Long id=((Number)imported.get("id")).longValue();
        assertEquals(1,cards.listAvailable("alice").size());assertTrue(cards.listAvailable("bob").isEmpty());
        assertThrows(ResponseStatusException.class,()->studio.detail("bob",id));assertThrows(RuntimeException.class,()->cards.activate("bob",id));
        cards.activate("alice",id);assertEquals(true,studio.configuration("alice").get("enabled"));assertTrue(cards.buildRolePrompt("alice").contains("独立实验"));
        var config=json.valueToTree(imported.get("config"));((com.fasterxml.jackson.databind.node.ObjectNode)config).put("personality","轻快，不催促");
        studio.save("alice",id,config);assertTrue(studio.exportJson("alice",id).toString().contains("轻快，不催促"));
        assertFalse(studio.configuration("bob").containsKey("enabled"));
    }
    @Test void disablingFlagHidesExperimentalCardsAndRestoresLegacyContext() throws Exception {
        var saved=studio.save("alice",null,json.readTree("{\"name\":\"实验声音\"}"));Long id=((Number)saved.get("id")).longValue();cards.activate("alice",id);
        ReflectionTestUtils.setField(access,"enabled",false);
        try {assertTrue(cards.listAvailable("alice").isEmpty());assertTrue(studio.configuration("alice").isEmpty());assertThrows(ResponseStatusException.class,()->cards.activate("alice",id));assertTrue(cards.buildRolePrompt("alice").contains("默认"));}
        finally{ReflectionTestUtils.setField(access,"enabled",true);}
    }
    @Test void defaultFlagIsOffAndWildcardDoesNotOpenItToAllUsers(){var flag=new RoleStudioAccess();assertFalse(flag.allowed("alice"));ReflectionTestUtils.setField(flag,"enabled",true);ReflectionTestUtils.setField(flag,"users","*");assertFalse(flag.allowed("alice"));}
}
