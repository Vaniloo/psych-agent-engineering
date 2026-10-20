package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Verifies an application-issued model decision against the authoritative turn. */
public final class GoalModelApproval {
    private GoalModelApproval() {}

    public static boolean verify(String token,String secret,String modelSha,String owner,Long sessionId,
            String requestId,String source,String operation,Long goalId,String actionId,Long version,String outcome) {
        return verifyAt(token,secret,modelSha,owner,sessionId,requestId,source,operation,goalId,actionId,version,outcome,
                Instant.now().getEpochSecond());
    }

    static boolean verifyAt(String token,String secret,String modelSha,String owner,Long sessionId,
            String requestId,String source,String operation,Long goalId,String actionId,Long version,String outcome,long now) {
        if(token==null||token.length()>2048||secret==null||secret.isBlank()||modelSha==null||modelSha.length()!=64)return false;
        try {
            String[] parts=token.split("\\.",-1);if(parts.length!=2)return false;
            Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
            String signature=HexFormat.of().formatHex(mac.doFinal(parts[0].getBytes(StandardCharsets.US_ASCII)));
            if(!MessageDigest.isEqual(signature.getBytes(StandardCharsets.US_ASCII),parts[1].getBytes(StandardCharsets.US_ASCII)))return false;
            var body=new ObjectMapper().readTree(Base64.getUrlDecoder().decode(parts[0]));
            if(!body.path("expires").isIntegralNumber()||body.path("expires").asLong()<now||body.path("expires").asLong()>now+310)return false;
            String sha=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(source.getBytes(StandardCharsets.UTF_8)));
            if(!"psych-goal-gate-v1".equals(body.path("contract").asText())||!modelSha.equals(body.path("modelSha").asText())
                    ||!owner.equals(body.path("owner").asText())||!body.path("sessionId").isIntegralNumber()
                    ||sessionId!=body.path("sessionId").asLong()||!requestId.equals(body.path("requestId").asText())
                    ||!sha.equals(body.path("sourceSha").asText())||!operation.equals(body.path("operation").asText()))return false;
            if("create".equals(operation))return !body.has("goalId")&&!body.has("actionId");
            if(java.util.Set.of("revise","complete","pause").contains(operation))
                return body.path("goalId").isIntegralNumber()&&goalId==body.path("goalId").asLong()
                        &&body.path("version").isIntegralNumber()&&version==body.path("version").asLong()
                        &&"".equals(body.path("actionId").asText())&&outcome.equals(body.path("outcome").asText());
            return "progress".equals(operation)&&body.path("goalId").isIntegralNumber()&&goalId==body.path("goalId").asLong()
                    &&actionId.equals(body.path("actionId").asText())&&body.path("version").isIntegralNumber()
                    &&version==body.path("version").asLong()&&outcome.equals(body.path("outcome").asText());
        } catch(Exception ignored){return false;}
    }
}
