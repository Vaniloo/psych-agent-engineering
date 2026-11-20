package com.vanilo.psych.agent.service;

import com.vanilo.psych.agent.dto.ReportResponse;
import com.vanilo.psych.agent.entity.PsychologicalReport;
import com.vanilo.psych.agent.entity.User;
import com.vanilo.psych.agent.repository.PsychologicalReportMapper;
import com.vanilo.psych.agent.repository.UserMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
public class ReportService {
    private final PsychologicalReportMapper psychologicalReportMapper;
    private final UserMapper userMapper;
    private ReportResponse toReportResponse(PsychologicalReport report) {
        return new ReportResponse(
                report.getId(),
                report.getMessage(),
                report.getRisk(),
                report.getEmotion(),
                report.getConfidence(),
                report.getCreatedAt(),
                report.getUser() != null ? report.getUser().getUsername() : null);
    }
    public ReportService(PsychologicalReportMapper psychologicalReportMapper, UserMapper userMapper) {
        this.psychologicalReportMapper = psychologicalReportMapper;
        this.userMapper = userMapper;
    }
    public List<ReportResponse> listAll(){
        return psychologicalReportMapper.findAll().stream().map(this::toReportResponse).toList();
    }
    public ReportResponse getPsychologicalReportById(long id){
        PsychologicalReport report=psychologicalReportMapper.findById(id).orElseThrow(()->new RuntimeException("报告不存在 id="+id));
    return toReportResponse(report);
    }


    @Transactional(readOnly = true)
    public Page<ReportResponse> listByPage(int page,int size){
        if (page < 0 ){
            page = 0;
        }
        if (size <= 0 ){
            size = 10;
        }
        Pageable pageable=PageRequest.of(page,size, Sort.by("createdAt").descending());
        return new PageImpl<>(psychologicalReportMapper.page(pageable), pageable, psychologicalReportMapper.count()).map(this::toReportResponse);
    }
    public List<ReportResponse> listMyReports(String username){
        User user=userMapper.findByUsername(username).orElseThrow(()->new RuntimeException("用户不存在！"));
        return psychologicalReportMapper.findByUser(user).stream().map(this::toReportResponse).toList();
    }

}
