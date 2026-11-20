package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.UserModelApi;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface UserModelApiMapper {
    @Select("SELECT * FROM user_model_api WHERE user_id = #{id}")
    Optional<UserModelApi> findById(Long id);

    @Delete("DELETE FROM user_model_api WHERE user_id = #{id}")
    int deleteById(Long id);

    @Insert("""
            INSERT INTO user_model_api
                (user_id, base_url, model, encrypted_key)
            VALUES
                (#{userId}, #{baseUrl}, #{model}, #{encryptedKey})
            ON DUPLICATE KEY UPDATE
                base_url = #{baseUrl}, model = #{model}, encrypted_key = #{encryptedKey}
            """)
    int upsert(UserModelApi row);
}
