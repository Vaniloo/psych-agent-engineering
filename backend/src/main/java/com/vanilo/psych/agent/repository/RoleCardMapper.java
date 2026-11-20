package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.RoleCard;
import com.vanilo.psych.agent.entity.User;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface RoleCardMapper {
    @Select("SELECT * FROM role_cards WHERE id = #{id}")
    @Results(id = "row", value = {
        @Result(column = "owner_id", property = "owner.id")
    })
    Optional<RoleCard> findById(Long id);

    @Select("SELECT * FROM role_cards WHERE preset = TRUE ORDER BY id")
    @ResultMap("row")
    List<RoleCard> presets();

    @Select("SELECT * FROM role_cards WHERE owner_id = #{owner.id} ORDER BY created_at DESC, id DESC")
    @ResultMap("row")
    List<RoleCard> byOwner(@Param("owner") User owner);

    @Select("SELECT * FROM role_cards WHERE name = #{name} AND preset = TRUE LIMIT 1")
    @ResultMap("row")
    Optional<RoleCard> presetNamed(String name);

    @Insert("""
            INSERT INTO role_cards
                (owner_id, name, description, tone, response_style, custom_instructions,
                 forbidden_expressions, studio_config_json, source_card_json, avatar_png, experimental,
                 preset, created_at)
            VALUES
                (#{owner.id}, #{name}, #{description}, #{tone}, #{responseStyle}, #{customInstructions},
                 #{forbiddenExpressions}, #{studioConfigJson}, #{sourceCardJson}, #{avatarPng},
                 #{experimental}, #{preset}, #{createdAt})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(RoleCard row);

    @Update("""
            UPDATE role_cards SET
                name = #{name}, description = #{description}, tone = #{tone}, response_style =
                #{responseStyle}, custom_instructions = #{customInstructions}, forbidden_expressions =
                #{forbiddenExpressions}, studio_config_json = #{studioConfigJson}, source_card_json =
                #{sourceCardJson}, avatar_png = #{avatarPng}, experimental = #{experimental}
            WHERE id = #{id}
            """)
    int update(RoleCard row);

    default RoleCard save(RoleCard row) {
        if (row.getId() == null) insert(row);
        else update(row);
        return row;
    }
}
