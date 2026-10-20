package com.vanilo.psych.agent.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.vanilo.psych.agent.service.RoleStudioService;
import org.springframework.http.*;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController @RequestMapping("/role-cards/studio")
public class RoleStudioController {
    private final RoleStudioService studio;
    public RoleStudioController(RoleStudioService studio){this.studio=studio;}
    public record ImportRequest(String format,String data){}
    @GetMapping("/features") public Map<String,Object> features(Authentication a){return studio.features(a.getName());}
    @PostMapping("/preview") public Map<String,Object> preview(Authentication a,@RequestBody ImportRequest r){return studio.preview(a.getName(),r.format(),r.data());}
    @PostMapping("/import") public Map<String,Object> importCard(Authentication a,@RequestBody ImportRequest r){return studio.importCard(a.getName(),r.format(),r.data());}
    @PostMapping public Map<String,Object> create(Authentication a,@RequestBody JsonNode r){return studio.save(a.getName(),null,r);}
    @GetMapping("/{id}") public Map<String,Object> detail(Authentication a,@PathVariable Long id){return studio.detail(a.getName(),id);}
    @PutMapping("/{id}") public Map<String,Object> update(Authentication a,@PathVariable Long id,@RequestBody JsonNode r){return studio.save(a.getName(),id,r);}
    @PostMapping("/{id}/copy") public Map<String,Object> copy(Authentication a,@PathVariable Long id){return studio.copy(a.getName(),id);}
    @GetMapping("/{id}/export") public ResponseEntity<?> export(Authentication a,@PathVariable Long id,@RequestParam(defaultValue="json")String format){
        if(format.equals("png"))return ResponseEntity.ok().contentType(MediaType.IMAGE_PNG).cacheControl(CacheControl.noStore()).body(studio.exportPng(a.getName(),id));
        if(!format.equals("json"))throw new org.springframework.web.server.ResponseStatusException(HttpStatus.BAD_REQUEST);
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(studio.exportJson(a.getName(),id));
    }
}
