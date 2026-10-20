package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.entity.UserModelApi;
import com.vanilo.psych.agent.repository.UserModelApiRepository;
import com.vanilo.psych.agent.repository.UserRepository;
import org.junit.jupiter.api.*;
import org.springframework.web.server.ResponseStatusException;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class UserModelApiServiceTests {
    UserModelApiService service; Map<Long,UserModelApi> rows; AgentRuntimeClient runtime;
    @BeforeEach void setup() {
        var repository=mock(UserModelApiRepository.class);var users=mock(UserRepository.class);runtime=mock(AgentRuntimeClient.class);rows=new HashMap<>();
        for(var name:List.of("alice","bob")){var user=new User();user.setId(name.equals("alice")?1L:2L);when(users.findByUsername(name)).thenReturn(Optional.of(user));}
        when(repository.findById(anyLong())).thenAnswer(i->Optional.ofNullable(rows.get(i.getArgument(0))));
        when(repository.saveAndFlush(any())).thenAnswer(i->{UserModelApi r=i.getArgument(0);rows.put(r.getUserId(),r);return r;});
        doAnswer(i->{rows.remove(i.getArgument(0));return null;}).when(repository).deleteById(anyLong());
        service=new UserModelApiService(repository,users,runtime,Base64.getEncoder().encodeToString(new byte[32]));
    }
    @Test void secretIsEncryptedAtRestAndNeverReturnedByPublicView() throws Exception {
        service.save("alice",new UserModelApiService.Input("https://provider.example/v1","model-a","dummy-private"));
        assertFalse(rows.get(1L).getEncryptedKey().contains("dummy-private"));
        assertFalse(new ObjectMapper().writeValueAsString(service.view("alice")).contains("dummy-private"));
        assertFalse(new ObjectMapper().writeValueAsString(rows.get(1L)).contains("encryptedKey"));
        assertEquals("dummy-private",service.connection("alice").get("apiKey"));
        assertEquals(false,service.connection("bob").get("enabled"));
    }
    @Test void changingDestinationRequiresNewKeyAndBlankKeyOnlyPreservesSameEndpoint() {
        service.save("alice",new UserModelApiService.Input("https://provider.example/v1","a","dummy-private"));
        service.save("alice",new UserModelApiService.Input("https://provider.example/v1/","b",""));
        assertEquals("dummy-private",service.connection("alice").get("apiKey"));
        assertThrows(ResponseStatusException.class,()->service.save("alice",new UserModelApiService.Input("https://another.example/v1","b","")));
        service.reset("alice");assertEquals(false,service.connection("alice").get("enabled"));
    }
    @Test void cipherIsBoundToAccountAndCannotBeMovedBetweenUsers() {
        service.save("alice",new UserModelApiService.Input("https://provider.example/v1","a","dummy-private"));
        var copied=new UserModelApi();copied.setUserId(2L);copied.setBaseUrl("https://provider.example/v1");copied.setModel("a");copied.setEncryptedKey(rows.get(1L).getEncryptedKey());rows.put(2L,copied);
        assertThrows(ResponseStatusException.class,()->service.connection("bob"));
    }
    @Test void malformedEndpointsAreRejectedWithoutEchoingInput() {
        assertThrows(ResponseStatusException.class,()->service.save("alice",new UserModelApiService.Input("https://user:secret@example.com/v1","a","dummy")));
        assertEquals("https://provider.example/v1",UserModelApiService.normalizeBaseUrl("https://provider.example/v1/chat/completions"));
    }
}
