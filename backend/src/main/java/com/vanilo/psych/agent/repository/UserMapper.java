package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.User;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface UserMapper {
    @Select("SELECT * FROM users WHERE username = #{username}")
    @Results(id = "row", value = {
        @Result(column = "active_role_card_id", property = "activeRoleCard.id")
    })
    Optional<User> findByUsername(String username);

    @Select("SELECT * FROM users WHERE username = #{username} FOR UPDATE")
    @ResultMap("row")
    Optional<User> lockByUsername(String username);

    @Select("SELECT * FROM users")
    @ResultMap("row")
    List<User> findAll();

    @Select("SELECT COUNT(*) FROM users")
    long count();

    @Insert("""
            INSERT INTO users
                (username, password, role, active_role_card_id)
            VALUES
                (#{username}, #{password}, #{role}, #{activeRoleCard.id})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(User row);

    @Update("UPDATE users SET active_role_card_id = #{activeRoleCard.id} WHERE id = #{id}")
    int setActiveCard(User user);

}
